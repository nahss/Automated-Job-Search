"""Show HN -- optional extra early-stage launches (Leads stream).

GET https://hn.algolia.com/api/v1/search_by_date?tags=show_hn

Off by default (config.SOURCES_ENABLED["showhn"]). When on, each Show HN
post becomes a lead and gets an opener drafted in scoring.py, same as
Product Hunt leads. Tagline (tags[0]) is derived from the title.
"""

from __future__ import annotations

from .. import config
from ..models import Listing, STREAM_LEAD
from ..util import clip, get_json, log, strip_html, to_iso

_URL = "https://hn.algolia.com/api/v1/search_by_date"


def fetch() -> list[Listing]:
    cap = config.FETCH_CAPS.get("showhn", 30)
    try:
        data = get_json(_URL, params={"tags": "show_hn", "hitsPerPage": cap})
    except Exception as e:
        log.warning("showhn: fetch failed: %s", e)
        return []

    listings: list[Listing] = []
    for hit in data.get("hits", []) or []:
        if not isinstance(hit, dict):
            continue
        oid = str(hit.get("objectID", "")).strip()
        if not oid:
            continue
        title = hit.get("title", "") or ""
        # Strip the leading "Show HN: " for a cleaner product name/tagline.
        name = title.split(":", 1)[-1].strip() if title.lower().startswith(
            "show hn") else title
        body = strip_html(hit.get("story_text", "") or "")
        listings.append(Listing(
            source="showhn",
            source_id=oid,
            stream=STREAM_LEAD,
            title=name or title,
            company=name or title,
            description=clip(body or name),
            tags=[name],  # tags[0] = tagline
            salary="",
            url=hit.get("url") or f"https://news.ycombinator.com/item?id={oid}",
            posted_at=to_iso(hit.get("created_at", "")),
            maker=hit.get("author", "") or "",
            contact=f"HN: {hit.get('author', '')}" if hit.get("author") else "",
            website=hit.get("url", "") or "",
        ))

    log.info("showhn: %d leads", len(listings))
    return listings
