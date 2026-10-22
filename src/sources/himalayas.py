"""Himalayas -- keyless JSON remote jobs.  [verify endpoint if it moves]

GET https://himalayas.app/jobs/api?limit=N

The payload has used both {"jobs": [...]} and {"data": [...]} shapes over
time, so we locate the list defensively.
"""

from __future__ import annotations

from typing import Any

from .. import config
from ..models import Listing, STREAM_JOB
from ..util import clip, get_json, log, strip_html, to_iso

_URL = "https://himalayas.app/jobs/api"


def _rows(data: Any) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("jobs", "data", "results"):
            v = data.get(key)
            if isinstance(v, list):
                return v
    return []


def _first(item: dict, *keys: str) -> Any:
    for k in keys:
        v = item.get(k)
        if v not in (None, ""):
            return v
    return ""


def fetch() -> list[Listing]:
    limit = config.FETCH_CAPS.get("himalayas", 50)
    try:
        data = get_json(_URL, params={"limit": limit})
    except Exception as e:
        log.warning("himalayas: fetch failed: %s", e)
        return []

    listings: list[Listing] = []
    for job in _rows(data):
        if not isinstance(job, dict):
            continue
        jid = str(_first(job, "guid", "id", "applicationLink", "url")).strip()
        if not jid:
            continue
        cats = _first(job, "categories", "jobCategories")
        tags = [str(c) for c in cats] if isinstance(cats, list) else []
        listings.append(Listing(
            source="himalayas",
            source_id=jid,
            stream=STREAM_JOB,
            title=str(_first(job, "title", "jobTitle")),
            company=str(_first(job, "companyName", "company", "company_name")),
            description=clip(strip_html(str(
                _first(job, "description", "excerpt")))),
            tags=tags,
            salary=str(_first(job, "salary", "")) or "",
            url=str(_first(job, "applicationLink", "url", "link")),
            posted_at=to_iso(_first(job, "pubDate", "publishedDate", "")),
        ))

    log.info("himalayas: %d listings", len(listings))
    return listings
