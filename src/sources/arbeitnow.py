"""Arbeitnow -- keyless JSON job board (mostly European).

GET https://www.arbeitnow.com/api/job-board-api

Filter on the `remote` boolean since David wants remote/global roles.
"""

from __future__ import annotations

from .. import config
from ..models import Listing, STREAM_JOB
from ..util import clip, get_json, log, strip_html, to_iso

_URL = "https://www.arbeitnow.com/api/job-board-api"


def fetch() -> list[Listing]:
    try:
        data = get_json(_URL)
    except Exception as e:
        log.warning("arbeitnow: fetch failed: %s", e)
        return []

    rows = data.get("data", []) if isinstance(data, dict) else []
    cap = config.FETCH_CAPS.get("arbeitnow", 100)
    listings: list[Listing] = []

    for job in rows or []:
        if not isinstance(job, dict):
            continue
        if not job.get("remote", False):
            continue
        slug = str(job.get("slug", "")).strip()
        if not slug:
            continue
        tags = job.get("tags") or []
        if not isinstance(tags, list):
            tags = []
        listings.append(Listing(
            source="arbeitnow",
            source_id=slug,
            stream=STREAM_JOB,
            title=job.get("title", "") or "",
            company=job.get("company_name", "") or "",
            description=clip(strip_html(job.get("description", "") or "")),
            tags=[str(t) for t in tags],
            salary="",
            url=job.get("url", "") or "",
            posted_at=to_iso(job.get("created_at", "")),
        ))
        if len(listings) >= cap:
            break

    log.info("arbeitnow: %d remote listings", len(listings))
    return listings
