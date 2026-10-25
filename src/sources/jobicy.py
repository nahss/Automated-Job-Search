"""Jobicy -- keyless JSON remote jobs.  [endpoint verified against docs]

GET https://jobicy.com/api/v2/remote-jobs?count=N
    (also supports geo, industry, tag params)

Field names have varied across Jobicy API versions, so we read each value
through a small list of candidate keys and fall back gracefully.
"""

from __future__ import annotations

from typing import Any

from .. import config
from ..models import Listing, STREAM_JOB
from ..util import clip, get_json, log, strip_html, to_iso

_URL = "https://jobicy.com/api/v2/remote-jobs"


def _first(item: dict, *keys: str) -> Any:
    for k in keys:
        v = item.get(k)
        if v not in (None, ""):
            return v
    return ""


def fetch() -> list[Listing]:
    count = config.FETCH_CAPS.get("jobicy", 50)
    try:
        data = get_json(_URL, params={"count": count})
    except Exception as e:
        log.warning("jobicy: fetch failed: %s", e)
        return []

    rows = data.get("jobs", []) if isinstance(data, dict) else []
    listings: list[Listing] = []

    for job in rows or []:
        if not isinstance(job, dict):
            continue
        jid = str(_first(job, "id", "jobId", "url")).strip()
        if not jid:
            continue
        industry = _first(job, "jobIndustry", "industry")
        job_type = _first(job, "jobType", "type")
        tags = []
        for val in (industry, job_type):
            if isinstance(val, list):
                tags.extend(str(v) for v in val)
            elif val:
                tags.append(str(val))
        listings.append(Listing(
            source="jobicy",
            source_id=jid,
            stream=STREAM_JOB,
            title=str(_first(job, "jobTitle", "title")),
            company=str(_first(job, "companyName", "company")),
            description=clip(strip_html(str(
                _first(job, "jobExcerpt", "jobDescription", "description")))),
            tags=tags,
            salary=str(_first(job, "annualSalaryMin", "salary", "")) or "",
            location=str(_first(job, "jobGeo", "geo", "")),
            url=str(_first(job, "url", "link")),
            posted_at=to_iso(_first(job, "pubDate", "date", "")),
        ))

    log.info("jobicy: %d listings", len(listings))
    return listings
