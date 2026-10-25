"""The Muse -- keyless public jobs API, used here for AFRICA-located roles.

GET https://www.themuse.com/api/public/jobs?page=N&location=City%2C%20Country

The Muse filters by structured location (not free-text search), so we query
each African location in config.THEMUSE_LOCATIONS and keyword-filter the
results to David's target categories. Listings carry their location, so the
pipeline's Africa reroute drops them into the Africa tab.

Keyless works (lower rate limit); an optional api_key raises it. We stay
keyless and gentle (a couple of pages per location, with delays).
"""

from __future__ import annotations

from .. import config
from ..models import Listing, STREAM_JOB
from ..util import clip, get_json, log, matches_any, polite_sleep, strip_html

_URL = "https://www.themuse.com/api/public/jobs"


def fetch() -> list[Listing]:
    pages = config.FETCH_CAPS.get("themuse", 2)
    locations = config.THEMUSE_LOCATIONS
    out: list[Listing] = []
    seen: set[str] = set()

    for loc in locations:
        for page in range(pages):
            try:
                data = get_json(_URL, params={"page": page, "location": loc})
            except Exception as e:
                log.warning("themuse[%s p%d]: fetch failed: %s", loc, page, e)
                break
            results = data.get("results", []) if isinstance(data, dict) else []
            if not results:
                break
            for j in results:
                if not isinstance(j, dict):
                    continue
                jid = str(j.get("id", "")).strip()
                if not jid or jid in seen:
                    continue
                title = j.get("name", "") or ""
                contents = strip_html(j.get("contents", "") or "")
                cats = [c.get("name", "") for c in (j.get("categories") or [])
                        if isinstance(c, dict)]
                locs = [x.get("name", "") for x in (j.get("locations") or [])
                        if isinstance(x, dict)]
                if not matches_any(f"{title}\n{' '.join(cats)}\n{contents}",
                                   config.CATEGORY_KEYWORDS):
                    continue
                seen.add(jid)
                company = (j.get("company") or {}).get("name", "") \
                    if isinstance(j.get("company"), dict) else ""
                out.append(Listing(
                    source="themuse",
                    source_id=jid,
                    stream=STREAM_JOB,
                    title=title,
                    company=company,
                    description=clip(contents),
                    tags=[t for t in (cats + locs) if t],
                    salary="",
                    location=", ".join(x for x in locs if x),
                    url=(j.get("refs") or {}).get("landing_page", "")
                        if isinstance(j.get("refs"), dict) else "",
                    posted_at=j.get("publication_date", "") or "",
                ))
            polite_sleep()
            pc = data.get("page_count")
            if isinstance(pc, int) and page + 1 >= pc:
                break

    log.info("themuse: %d matched across %d African locations",
             len(out), len(locations))
    return out
