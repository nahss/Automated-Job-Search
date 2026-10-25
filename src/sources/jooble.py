"""Jooble -- jobs aggregator API (free key). Covers many African countries
(Nigeria, Kenya, Ghana, South Africa, Egypt, ...).

POST https://jooble.org/api/{key}  with JSON {"keywords": "...", "location": "..."}

Needs JOOBLE_API_KEY (free at jooble.org/api/about). Without it the source
skips with a warning. We query each keyword x location (lists kept short in
config). Results carry an African location, so the Africa reroute places them
in the Africa tab.
"""

from __future__ import annotations

import os

from .. import config
from ..models import Listing, STREAM_JOB
from ..util import clip, log, matches_any, polite_sleep, post_json, strip_html

_URL = "https://jooble.org/api/{key}"


def fetch() -> list[Listing]:
    key = os.environ.get("JOOBLE_API_KEY")
    if not key:
        log.warning("jooble: JOOBLE_API_KEY not set; skipping")
        return []

    url = _URL.format(key=key)
    out: list[Listing] = []
    seen: set[str] = set()

    for loc in config.JOOBLE_LOCATIONS:
        for kw in config.JOOBLE_KEYWORDS:
            try:
                data = post_json(url, json_body={"keywords": kw, "location": loc})
            except Exception as e:
                log.warning("jooble[%s/%s]: fetch failed: %s", loc, kw, e)
                continue
            for j in (data.get("jobs", []) if isinstance(data, dict) else []):
                if not isinstance(j, dict):
                    continue
                jid = str(j.get("id") or j.get("link") or "").strip()
                if not jid or jid in seen:
                    continue
                title = j.get("title", "") or ""
                snippet = strip_html(j.get("snippet", "") or "")
                if not matches_any(f"{title}\n{snippet}", config.CATEGORY_KEYWORDS):
                    continue
                seen.add(jid)
                out.append(Listing(
                    source="jooble",
                    source_id=jid,
                    stream=STREAM_JOB,
                    title=title,
                    company=j.get("company", "") or "",
                    description=clip(snippet),
                    tags=[j.get("type", "")] if j.get("type") else [],
                    salary=j.get("salary", "") or "",
                    location=j.get("location", "") or loc,
                    url=j.get("link", "") or "",
                    posted_at=j.get("updated", "") or "",
                ))
            polite_sleep()

    log.info("jooble: %d matched across %d locations",
             len(out), len(config.JOOBLE_LOCATIONS))
    return out
