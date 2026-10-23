"""Pipeline entry point: Pull -> Score -> Dedupe -> Write.

Run:
    python -m src.main              # full run (needs env vars + sheet)
    python -m src.main --dry-run    # fetch + score, print instead of writing
    python -m src.main --only remoteok reddit_gigs   # subset of sources

A failing source logs a warning and is skipped; the run continues and still
writes whatever else succeeded. Dedupe is against the sheet itself, so
running twice back-to-back adds zero duplicate rows.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict
from dataclasses import dataclass

try:
    from dotenv import load_dotenv
    load_dotenv()  # local .env; a no-op in CI where secrets come from env
except Exception:
    pass

from . import config
from .models import Listing, STREAM_JOB, STREAM_GIG, STREAM_LEAD
from .sources import REGISTRY
from .util import log, matches_any, setup_logging

# Streams in sheet-writing order, with human labels for the summary.
_STREAMS = [(STREAM_JOB, "Jobs"), (STREAM_GIG, "Gigs"), (STREAM_LEAD, "Leads")]

# Extra terms (besides the pitch-deck list) that reroute a job -> gig.
_FREELANCE_TERMS = ["freelance", "contract", "part-time", "part time",
                    "project-based", "per project"]


@dataclass
class Tagged:
    """A listing plus the registry-source name it came from (for counting)."""
    name: str
    listing: Listing


# --------------------------------------------------------------------------
# Stages
# --------------------------------------------------------------------------

def pull(only: list[str] | None) -> tuple[list[Tagged], dict[str, int]]:
    """Fetch every enabled source. Returns tagged listings + per-source
    fetched counts. Each source is wrapped so one failure can't stop the run."""
    tagged: list[Tagged] = []
    fetched: dict[str, int] = {}
    for name, fetch_fn in REGISTRY.items():
        if only and name not in only:
            continue
        if not config.SOURCES_ENABLED.get(name, False):
            log.info("%s: disabled, skipping", name)
            continue
        try:
            items = fetch_fn() or []
        except Exception as e:  # belt-and-braces; sources catch internally too
            log.warning("%s: unhandled error, skipping: %s", name, e)
            fetched[name] = 0
            continue
        fetched[name] = len(items)
        tagged.extend(Tagged(name, l) for l in items)
    return tagged, fetched


def reroute_gigs(tagged: list[Tagged]) -> None:
    """Jobs matching pitch-deck / freelance keywords move to the Gigs tab."""
    terms = config.PITCH_DECK_KEYWORDS + _FREELANCE_TERMS
    moved = 0
    for t in tagged:
        if t.listing.stream != STREAM_JOB:
            continue
        blob = f"{t.listing.title} {' '.join(t.listing.tags)} {t.listing.description}"
        if matches_any(blob, terms):
            t.listing.stream = STREAM_GIG
            moved += 1
    if moved:
        log.info("rerouted %d job(s) -> gigs (pitch-deck/freelance match)", moved)


def dedupe_in_memory(tagged: list[Tagged]) -> list[Tagged]:
    """Drop duplicate job_ids within this run (feeds re-serve overlap)."""
    seen: set[str] = set()
    out: list[Tagged] = []
    for t in tagged:
        jid = t.listing.job_id
        if jid in seen:
            continue
        seen.add(jid)
        out.append(t)
    return out


def filter_recent(tagged: list[Tagged]) -> list[Tagged]:
    """Keep only listings posted within config.MAX_POSTING_AGE_DAYS.

    Listings with no parseable date are kept (or dropped if
    DROP_UNDATED_LISTINGS) so a good role isn't lost to missing metadata.
    """
    max_days = config.MAX_POSTING_AGE_DAYS
    if not max_days or max_days <= 0:
        return tagged
    from datetime import datetime, timedelta, timezone
    from .util import parse_dt
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_days)
    out: list[Tagged] = []
    dropped_old = dropped_undated = 0
    for t in tagged:
        dt = parse_dt(t.listing.posted_at)
        if dt is None:
            if config.DROP_UNDATED_LISTINGS:
                dropped_undated += 1
            else:
                out.append(t)
        elif dt >= cutoff:
            out.append(t)
        else:
            dropped_old += 1
    if dropped_old or dropped_undated:
        log.info("recency: dropped %d older than %dd%s",
                 dropped_old, max_days,
                 f" + {dropped_undated} undated" if dropped_undated else "")
    return out


def interleave_by_source(items: list[Tagged]) -> list[Tagged]:
    """Round-robin items across their source so the per-stream scoring cap is
    shared fairly. Without this, whichever source is listed first (a big job
    board) would fill the cap and starve the rest (e.g. company career pages).
    Order within each source is preserved."""
    from collections import OrderedDict, deque
    buckets: "OrderedDict[str, deque]" = OrderedDict()
    for t in items:
        buckets.setdefault(t.name, deque()).append(t)
    queues = list(buckets.values())
    out: list[Tagged] = []
    while queues:
        queues = [q for q in queues if q]
        for q in queues:
            out.append(q.popleft())
    return out


def score_stream(stream: str, items: list[Tagged],
                 cv: str) -> tuple[list[Tagged], int, int]:
    """Score (or draft, for leads) and keep only those >= threshold.

    Returns (kept items sorted by score desc, attempts, failures) so the
    caller can detect a wholesale LLM outage (every call failing).
    """
    from . import scoring
    kept: list[Tagged] = []
    attempts = failures = 0
    for t in items:
        l = t.listing
        attempts += 1
        if stream == STREAM_LEAD:
            res = scoring.draft_lead(l, cv)
            if res is None:
                failures += 1
                continue
            fit, opener = res
            l.score, l.opener = fit, opener
            if fit >= config.SCORE_THRESHOLD:
                kept.append(t)
        else:
            res = scoring.score_listing(l, cv)
            if res is None:
                failures += 1
                continue
            score, reason = res
            l.score, l.reason = score, reason
            if score >= config.SCORE_THRESHOLD:
                kept.append(t)
    kept.sort(key=lambda t: (t.listing.score or 0), reverse=True)
    return kept, attempts, failures


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def run(dry_run: bool, only: list[str] | None) -> int:
    from . import scoring

    tagged, fetched = pull(only)
    reroute_gigs(tagged)
    tagged = dedupe_in_memory(tagged)
    tagged = filter_recent(tagged)

    # Group by stream, preserving source names.
    by_stream: dict[str, list[Tagged]] = defaultdict(list)
    for t in tagged:
        by_stream[t.listing.stream].append(t)

    # Scoring needs an LLM. In dry-run we tolerate its absence (fetch-only).
    have_llm = scoring.llm_available()
    if not have_llm:
        msg = ("No LLM configured (set ANTHROPIC_API_KEY, or OPENAI_API_KEY "
               "with LLM_PROVIDER=openai).")
        if not dry_run:
            log.error(msg + " Cannot score; aborting.")
            return 2
        log.warning(msg + " Dry-run will fetch only, without scoring.")

    cv = scoring.load_cv()

    # Connect to the sheet (skip entirely on dry-run).
    client = None
    if not dry_run:
        from .sheets import SheetClient
        client = SheetClient.connect()

    new_counts: dict[str, int] = defaultdict(int)
    written_counts: dict[str, int] = defaultdict(int)
    total_attempts = total_failures = 0

    for stream, label in _STREAMS:
        items = by_stream.get(stream, [])
        if not items:
            continue

        # Dedupe against the sheet (source of truth). Dry-run keeps all.
        if client is not None:
            existing = client.existing_ids(stream)
            items = [t for t in items if t.listing.job_id not in existing]
        for t in items:
            new_counts[t.name] += 1

        # Interleave across sources so the cap is shared fairly, then cap how
        # many NEW listings we send to the LLM per stream.
        items = interleave_by_source(items)
        cap = config.MAX_SCORED_PER_STREAM
        if len(items) > cap:
            log.info("%s: capping %d new -> %d before scoring (interleaved)",
                     label, len(items), cap)
            items = items[:cap]

        if not have_llm:
            _print_fetch_only(label, items)
            continue

        kept, attempts, failures = score_stream(stream, items, cv)
        total_attempts += attempts
        total_failures += failures

        if dry_run:
            _print_scored(label, kept)
            continue

        from .sheets import write_listings
        written = write_listings(client, stream, [t.listing for t in kept])
        for t in kept:
            written_counts[t.name] += 1
        log.info("%s: wrote %d row(s)", label, written)

    _summary(fetched, new_counts, written_counts, dry_run)

    # Fail-loud alerting: a wholesale scoring outage (every LLM call failing)
    # would otherwise look like a quiet, successful run that wrote nothing --
    # exactly what an out-of-credits / bad-key / bad-model situation produces.
    # Exit non-zero so the GitHub Actions run goes red and notifies.
    if total_failures:
        log.warning("scoring: %d of %d LLM calls failed",
                    total_failures, total_attempts)
    if total_attempts > 0 and total_failures == total_attempts:
        detail = (f" Cause: {scoring.LAST_ERROR}" if scoring.LAST_ERROR
                  else " Check the 'score failed' warnings above.")
        log.error("ALL %d scoring calls failed -- nothing was written. "
                  "The LLM provider is likely out of credits, or the key/"
                  "model is wrong.%s", total_attempts, detail)
        return 3

    return 0


# --------------------------------------------------------------------------
# Output helpers
# --------------------------------------------------------------------------

def _print_fetch_only(label: str, items: list[Tagged]) -> None:
    print(f"\n=== {label}: {len(items)} fetched (scoring skipped) ===")
    for t in items[:15]:
        print(f"  - [{t.name}] {t.listing.title} @ {t.listing.company}")


def _print_scored(label: str, kept: list[Tagged]) -> None:
    print(f"\n=== {label}: {len(kept)} would be written (>= "
          f"{config.SCORE_THRESHOLD}) ===")
    for t in kept:
        l = t.listing
        print(f"  [{l.score:>3}] {l.title} @ {l.company}  ({t.name})")
        if l.stream == STREAM_LEAD and l.opener:
            print(f"        opener: {l.opener}")
        elif l.reason:
            print(f"        why: {l.reason}")


def _summary(fetched: dict[str, int], new: dict[str, int],
             written: dict[str, int], dry_run: bool) -> None:
    log.info("---- run summary%s ----", " (dry-run)" if dry_run else "")
    header = f"{'source':<16} {'fetched':>8} {'new':>6} {'written':>8}"
    log.info(header)
    for name in REGISTRY:
        f = fetched.get(name)
        if f is None:
            continue  # source not run this invocation
        log.info("%-16s %8d %6d %8d", name, f, new.get(name, 0),
                 written.get(name, 0))
    log.info("totals: fetched=%d new=%d written=%d",
             sum(fetched.values()), sum(new.values()), sum(written.values()))


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Daily Job & Lead Agent")
    parser.add_argument("--dry-run", action="store_true",
                        help="fetch + score, print instead of writing")
    parser.add_argument("--only", nargs="+", metavar="SOURCE",
                        help="run only these source names (see config)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="debug logging")
    args = parser.parse_args(argv)

    setup_logging(logging.DEBUG if args.verbose else logging.INFO)
    try:
        return run(dry_run=args.dry_run, only=args.only)
    except KeyboardInterrupt:
        log.warning("interrupted")
        return 130
    except Exception as e:
        log.error("fatal: %s", e, exc_info=args.verbose)
        return 1


if __name__ == "__main__":
    sys.exit(main())
