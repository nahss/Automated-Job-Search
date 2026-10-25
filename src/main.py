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
from .models import (Listing, STREAM_JOB, STREAM_GIG, STREAM_LEAD,
                     STREAM_AFRICA)
from .sources import REGISTRY
from .util import log, matches_any, setup_logging

# Streams in sheet-writing order, with human labels for the summary.
_STREAMS = [(STREAM_JOB, "Jobs"), (STREAM_AFRICA, "Africa"),
            (STREAM_GIG, "Gigs"), (STREAM_LEAD, "Leads")]

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
    """Drop duplicates within this run. Two kinds:
      - same job_id (a feed re-serving the same posting), and
      - same (company|role) across DIFFERENT sources -- e.g. a role listed on
        both RemoteOK and the company's own ATS board -- which have different
        job_ids but are the same job. Content dedupe applies to Jobs/Africa.
    """
    from .util import content_key
    seen_ids: set[str] = set()
    seen_content: set[str] = set()
    out: list[Tagged] = []
    for t in tagged:
        jid = t.listing.job_id
        if jid in seen_ids:
            continue
        if t.listing.stream in (STREAM_JOB, STREAM_AFRICA):
            ck = content_key(t.listing.company, t.listing.title)
            if ck and ck in seen_content:
                seen_ids.add(jid)
                continue
            if ck:
                seen_content.add(ck)
        seen_ids.add(jid)
        out.append(t)
    return out


def reroute_africa(tagged: list[Tagged]) -> None:
    """Jobs located in / targeting an African country move to the Africa tab."""
    from .util import is_african
    moved = 0
    for t in tagged:
        if t.listing.stream != STREAM_JOB:
            continue
        if is_african(t.listing.title, t.listing.location, t.listing.tags):
            t.listing.stream = STREAM_AFRICA
            moved += 1
    if moved:
        log.info("rerouted %d job(s) -> Africa tab", moved)


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


def filter_company_size(tagged: list[Tagged]) -> list[Tagged]:
    """Optionally drop listings from large companies (small teams are the
    stronger 'they need a designer' signal). Default keeps them (flagged by
    the Company-size column); the scoring prompt down-ranks them regardless."""
    if not config.DROP_LARGE_COMPANIES:
        return tagged
    out = [t for t in tagged if t.listing.company_size != "large"]
    dropped = len(tagged) - len(out)
    if dropped:
        log.info("size: dropped %d large-company listing(s)", dropped)
    return out


def filter_seniority(tagged: list[Tagged]) -> list[Tagged]:
    """Optional HARD cut: drop senior-titled Jobs/Gigs when configured. The
    scoring prompt already down-ranks senior roles softly; this is the strong
    lever. Founding / first-designer / 0->1 roles are always kept."""
    if not config.EXCLUDE_SENIOR_TITLES:
        return tagged
    from .util import title_is_senior
    out, dropped = [], 0
    for t in tagged:
        if (t.listing.stream in (STREAM_JOB, STREAM_GIG)
                and title_is_senior(t.listing.title)):
            dropped += 1
            continue
        out.append(t)
    if dropped:
        log.info("seniority: dropped %d senior-titled listing(s)", dropped)
    return out


# --------------------------------------------------------------------------
# Discovery: resolve company names -> live ATS boards, cached in the sheet.
# --------------------------------------------------------------------------

def _hn_company(title: str) -> str:
    """Best-effort company name from a HN 'Who is hiring' comment's first line
    (usually 'Company | Role | Location | ...')."""
    import re
    head = re.split(r"[|()\-–—:]", title or "", 1)[0].strip()
    return head if 1 < len(head) <= 40 else ""


def _candidate_names(tagged: list[Tagged]) -> list[str]:
    """Company names worth resolving, from this run's Leads + HN listings."""
    names: list[str] = []
    for t in tagged:
        l = t.listing
        if l.stream == STREAM_LEAD and config.DISCOVER_FROM_LEADS:
            names.append(l.company or l.title)
        elif t.name == "hackernews" and config.DISCOVER_FROM_HACKERNEWS:
            n = _hn_company(l.title)
            if n:
                names.append(n)
    return names


def _discover(names: list[str], attempted: set[str], source: str,
              budget: int) -> tuple[list[dict], list[list], int]:
    """Resolve uncached candidate names to ATS boards. Returns
    (new boards, watchlist rows to persist, number attempted this call).
    Records every attempt (hit or miss) so a name is resolved at most once."""
    import re
    from . import discovery
    from .util import now_local_str
    new_boards: list[dict] = []
    rows: list[list] = []
    processed: set[str] = set()
    resolved = 0
    today = now_local_str()
    for name in names:
        if budget <= 0:
            break
        # Cache key keeps dots so "cal.com" != "calcom" (distinct candidates).
        key = re.sub(r"[^a-z0-9.]+", "", (name or "").lower()).strip(".")
        if not key:
            continue
        if key in attempted or key in processed:
            continue
        processed.add(key)
        budget -= 1
        res = discovery.resolve(name)
        if res:
            ats, slug = res
            new_boards.append({"ats": ats, "slug": slug, "name": slug,
                               "source": source})
            rows.append([key, ats, slug, source, today])
            resolved += 1
        else:
            rows.append([key, "", "", source, today])
    attempted |= processed
    if processed:
        log.info("discovery[%s]: tried %d, resolved %d", source,
                 len(processed), resolved)
    return new_boards, rows, len(processed)


def _build_pitch(client, cv: str, dry_run: bool) -> int:
    """Companies with a LIVE board but zero matching design roles -> a cold-
    pitch list with an AI-drafted opener in the candidate's voice. Deduped
    against the Pitch tab so a company is pitched once."""
    from . import boards, scoring
    from .models import Listing, STREAM_PITCH
    from .util import company_size

    candidates = []
    for s in boards.stats():
        if not s["reachable"] or s["total"] <= 0 or s["matched"] > 0:
            continue  # unreachable / not hiring / already has a design role
        size = company_size(s["total"])
        if size not in config.PITCH_SIZES:
            continue  # pitch only small/mid teams by default
        candidates.append((s, size))
    if not candidates:
        return 0

    existing = client.existing_ids(STREAM_PITCH) if client is not None else set()
    new = [(s, size) for s, size in candidates
           if f"pitch:{s['ats']}:{s['slug']}" not in existing]
    if len(new) > config.MAX_PITCH_PER_RUN:
        log.info("pitch: capping %d new -> %d", len(new), config.MAX_PITCH_PER_RUN)
        new = new[:config.MAX_PITCH_PER_RUN]

    listings: list[Listing] = []
    for s, size in new:
        opener = scoring.draft_pitch(s["name"], s["titles"], cv)
        if not opener:
            continue
        listings.append(Listing(
            source=f"pitch:{s['ats']}", source_id=s["slug"],
            stream=STREAM_PITCH, company=s["name"], tags=[s["ats"]],
            open_roles=s["total"], company_size=size, opener=opener,
            url=boards.board_url(s["ats"], s["slug"])))

    if dry_run:
        print(f"\n=== Pitch: {len(listings)} companies (live board, no designer) ===")
        for l in listings:
            print(f"  {l.company} [{l.tags[0]}:{l.source_id}] "
                  f"({l.company_size}, {l.open_roles} roles)")
            print(f"        {l.opener}")
        return len(listings)

    from .sheets import write_listings
    n = write_listings(client, STREAM_PITCH, listings)
    log.info("Pitch: wrote %d company(ies)", n)
    return n


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
    from . import boards, scoring

    # Scoring needs an LLM. In dry-run we tolerate its absence (fetch-only).
    have_llm = scoring.llm_available()
    if not have_llm:
        msg = ("No LLM configured (set ANTHROPIC_API_KEY, or OPENAI_API_KEY "
               "with LLM_PROVIDER=openai).")
        if not dry_run:
            log.error(msg + " Cannot score; aborting.")
            return 2
        log.warning(msg + " Dry-run will fetch only, without scoring.")

    # Connect to the sheet FIRST: discovery reads its cache and injects any
    # discovered boards before the ATS adapters run. Skipped on dry-run.
    client = None
    attempted: set[str] = set()
    budget = config.RESOLVER_MAX_PER_RUN
    if not dry_run:
        from .sheets import SheetClient
        client = SheetClient.connect()
        if config.DISCOVERY_ENABLED:
            attempted, discovered = client.read_watchlist()
            boards.set_discovered(discovered)
            # Resolve uncached SEED_COMPANIES now so new boards are fetched
            # THIS run.
            nb, rows, n = _discover(config.SEED_COMPANIES, attempted,
                                    "seed", budget)
            budget -= n
            if rows:
                client.append_watchlist(rows)
            if nb:
                boards.set_discovered(discovered + nb)

    cv = scoring.load_cv()

    boards.reset_stats()
    tagged, fetched = pull(only)
    reroute_gigs(tagged)
    reroute_africa(tagged)
    tagged = dedupe_in_memory(tagged)

    # Discover boards from this run's Leads/HN companies (feeds the NEXT run).
    if client is not None and config.DISCOVERY_ENABLED and budget > 0:
        _, rows2, _ = _discover(_candidate_names(tagged), attempted,
                                "lead-hn", budget)
        if rows2:
            client.append_watchlist(rows2)

    tagged = filter_recent(tagged)
    tagged = filter_company_size(tagged)
    tagged = filter_seniority(tagged)

    # Group by stream, preserving source names.
    by_stream: dict[str, list[Tagged]] = defaultdict(list)
    for t in tagged:
        by_stream[t.listing.stream].append(t)

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
            # Also skip the same (company|role) already in the tab from
            # another source (Jobs/Africa only).
            if stream in (STREAM_JOB, STREAM_AFRICA):
                from .util import content_key
                seen_content = client.existing_content_keys(stream)
                if seen_content:
                    items = [t for t in items if content_key(
                        t.listing.company, t.listing.title) not in seen_content]
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

    # Cold-pitch list: companies with a live board but no design role.
    scoring_all_failed = total_attempts > 0 and total_failures == total_attempts
    if config.PITCH_ENABLED and have_llm and not scoring_all_failed:
        _build_pitch(client, cv, dry_run)

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
