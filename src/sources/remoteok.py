"""RemoteOK -- keyless JSON.

GET https://remoteok.com/api  (requires a descriptive User-Agent)

The FIRST array element is a legal/metadata notice, not a job -- we skip it.
"""

from __future__ import annotations

from .. import config
from ..models import Listing, STREAM_JOB
from ..util import clip, get_json, log, strip_html, to_iso

_URL = "https://remoteok.com/api"


def _salary(item: dict) -> str:
    lo, hi = item.get("salary_min"), item.get("salary_max")
    if lo and hi:
        return f"${lo:,} - ${hi:,}"
    if lo:
        return f"${lo:,}+"
    return ""


def fetch() -> list[Listing]:
    try:
        # RemoteOK is picky about UA; util already sends config.USER_AGENT.
        data = get_json(_URL)
    except Exception as e:
        log.warning("remoteok: fetch failed: %s", e)
        return []

    if not isinstance(data, list) or not data:
        log.warning("remoteok: unexpected response shape")
        return []

    cap = config.FETCH_CAPS.get("remoteok", 100)
    listings: list[Listing] = []
    # [1:] skips the legal notice element.
    for item in data[1:]:
        if not isinstance(item, dict):
            continue
        jid = str(item.get("id") or item.get("slug") or "").strip()
        if not jid:
            continue
        tags = item.get("tags") or []
        if not isinstance(tags, list):
            tags = []
        listings.append(Listing(
            source="remoteok",
            source_id=jid,
            stream=STREAM_JOB,
            title=item.get("position", "") or item.get("title", "") or "",
            company=item.get("company", "") or "",
            description=clip(strip_html(item.get("description", "") or "")),
            tags=[str(t) for t in tags],
            salary=_salary(item),
            url=item.get("url", "") or "",
            posted_at=to_iso(item.get("date", "")),
        ))
        if len(listings) >= cap:
            break

    log.info("remoteok: %d listings", len(listings))
    return listings
