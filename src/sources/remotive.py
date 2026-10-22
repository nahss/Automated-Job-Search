"""Remotive -- keyless JSON job board.

GET https://remotive.com/api/remote-jobs?limit=N[&category=...]

Remotive asks integrators to be gentle, so we cap total calls per run
(config.REMOTIVE_MAX_CALLS) and we credit Remotive by keeping its posting
URL as the listing link (the sheet's Source column shows "remotive").
"""

from __future__ import annotations

from .. import config
from ..models import Listing, STREAM_JOB
from ..util import clip, get_json, log, polite_sleep, strip_html, to_iso

_BASE = "https://remotive.com/api/remote-jobs"

# A few relevant category slugs. Total calls still capped by REMOTIVE_MAX_CALLS.
_CATEGORIES = ["design", "software-dev", "qa", "product"]


def fetch() -> list[Listing]:
    limit = config.FETCH_CAPS.get("remotive", 100)
    seen: set[str] = set()
    listings: list[Listing] = []
    calls = 0

    for category in _CATEGORIES:
        if calls >= config.REMOTIVE_MAX_CALLS:
            break
        params = {"limit": limit, "category": category}
        try:
            data = get_json(_BASE, params=params)
        except Exception as e:  # network / shape / rate limit
            log.warning("remotive[%s]: fetch failed: %s", category, e)
            calls += 1
            polite_sleep()
            continue
        calls += 1

        for job in data.get("jobs", []) or []:
            jid = str(job.get("id", "")).strip()
            if not jid or jid in seen:
                continue
            seen.add(jid)
            tags = job.get("tags") or []
            if not isinstance(tags, list):
                tags = []
            listings.append(Listing(
                source="remotive",
                source_id=jid,
                stream=STREAM_JOB,
                title=job.get("title", "") or "",
                company=job.get("company_name", "") or "",
                description=clip(strip_html(job.get("description", "") or "")),
                tags=[str(t) for t in tags],
                salary=job.get("salary", "") or "",
                url=job.get("url", "") or "",
                posted_at=to_iso(job.get("publication_date", "")),
            ))
        polite_sleep()

    log.info("remotive: %d listings (%d api calls)", len(listings), calls)
    return listings
