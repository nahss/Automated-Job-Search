"""Working Nomads -- keyless JSON feed.  [verify endpoint if it moves]

GET https://www.workingnomads.com/api/exposed_jobs/

Returns a flat list of jobs: url, title, company_name, category_name,
tags (comma string), description, pub_date, location.
"""

from __future__ import annotations

from typing import Any

from .. import config
from ..models import Listing, STREAM_JOB
from ..util import clip, get_json, log, strip_html, to_iso

_URL = "https://www.workingnomads.com/api/exposed_jobs/"


def _rows(data: Any) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("jobs", "data", "results"):
            v = data.get(key)
            if isinstance(v, list):
                return v
    return []


def fetch() -> list[Listing]:
    try:
        data = get_json(_URL)
    except Exception as e:
        log.warning("working_nomads: fetch failed: %s", e)
        return []

    cap = config.FETCH_CAPS.get("working_nomads", 50)
    listings: list[Listing] = []
    for job in _rows(data):
        if not isinstance(job, dict):
            continue
        url = str(job.get("url", "")).strip()
        jid = str(job.get("id") or url).strip()
        if not jid:
            continue
        raw_tags = job.get("tags", "")
        if isinstance(raw_tags, str):
            tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
        elif isinstance(raw_tags, list):
            tags = [str(t) for t in raw_tags]
        else:
            tags = []
        cat = job.get("category_name")
        if cat:
            tags.append(str(cat))
        listings.append(Listing(
            source="working_nomads",
            source_id=jid,
            stream=STREAM_JOB,
            title=job.get("title", "") or "",
            company=job.get("company_name", "") or "",
            description=clip(strip_html(job.get("description", "") or "")),
            tags=tags,
            salary="",
            url=url,
            posted_at=to_iso(job.get("pub_date", "")),
        ))
        if len(listings) >= cap:
            break

    log.info("working_nomads: %d listings", len(listings))
    return listings
