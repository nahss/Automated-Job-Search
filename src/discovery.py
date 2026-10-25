"""Discovery resolver: company name/slug -> live ATS board.

Ashby-first (it's the highest-yield ATS for the startup cohort we target --
most post-2018 startups use it, and application forms post to ashbyhq.com even
behind custom domains), then Greenhouse, then Lever. For each platform we try
the slug variants from util.slug_variants (e.g. "radicalai", "radical-ai").

A board "exists" when its public endpoint returns 200 with the expected JSON
shape. resolve() returns (ats, slug) of the first hit, or None.

Network note: these are live lookups. Callers must cache results (hit AND
miss) so a name is resolved at most once -- see main.py + the _Watchlist tab.
"""

from __future__ import annotations

from typing import Optional

from .util import get_json, log, slug_variants

_ASHBY = "https://api.ashbyhq.com/posting-api/job-board/{slug}"
_GREENHOUSE = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
_LEVER = "https://api.lever.co/v0/postings/{slug}"

_TIMEOUT = 12.0


def _ashby_exists(slug: str) -> bool:
    try:
        data = get_json(_ASHBY.format(slug=slug), timeout=_TIMEOUT)
    except Exception:
        return False
    return isinstance(data, dict) and isinstance(data.get("jobs"), list)


def _greenhouse_exists(slug: str) -> bool:
    try:
        data = get_json(_GREENHOUSE.format(slug=slug),
                        params={"content": "true"}, timeout=_TIMEOUT)
    except Exception:
        return False
    return isinstance(data, dict) and isinstance(data.get("jobs"), list)


def _lever_exists(slug: str) -> bool:
    try:
        data = get_json(_LEVER.format(slug=slug), params={"mode": "json"},
                        timeout=_TIMEOUT)
    except Exception:
        return False
    return isinstance(data, list)


# Ashby first, then Greenhouse, then Lever.
_CHECKS = (("ashby", _ashby_exists),
           ("greenhouse", _greenhouse_exists),
           ("lever", _lever_exists))


def resolve(name: str) -> Optional[tuple[str, str]]:
    """Return (ats, slug) for the first platform/variant that has a live board
    for `name`, or None. Variants are tried per platform, Ashby first."""
    variants = slug_variants(name)
    if not variants:
        return None
    for ats, check in _CHECKS:
        for slug in variants:
            if check(slug):
                log.info("discovery: %r -> %s/%s", name, ats, slug)
                return ats, slug
    return None
