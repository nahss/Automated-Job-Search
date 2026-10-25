"""Adzuna -- jobs API (free key). Used here for South Africa ("za"), the only
African country in Adzuna's coverage.

GET https://api.adzuna.com/v1/api/jobs/{country}/search/1
    ?app_id=..&app_key=..&what=..&results_per_page=50&content-type=application/json

Needs ADZUNA_APP_ID + ADZUNA_APP_KEY (free at developer.adzuna.com). Without
them the source skips with a warning. Results carry a South-African location,
so the Africa reroute places them in the Africa tab.
"""

from __future__ import annotations

import os

from .. import config
from ..models import Listing, STREAM_JOB
from ..util import clip, get_json, log, matches_any, polite_sleep, strip_html

_URL = "https://api.adzuna.com/v1/api/jobs/{country}/search/1"


def fetch() -> list[Listing]:
    app_id = os.environ.get("ADZUNA_APP_ID")
    app_key = os.environ.get("ADZUNA_APP_KEY")
    if not (app_id and app_key):
        log.warning("adzuna: ADZUNA_APP_ID/ADZUNA_APP_KEY not set; skipping")
        return []

    country = config.ADZUNA_COUNTRY
    out: list[Listing] = []
    seen: set[str] = set()

    for q in config.ADZUNA_QUERIES:
        try:
            data = get_json(_URL.format(country=country), params={
                "app_id": app_id, "app_key": app_key,
                "results_per_page": 50, "what": q,
                "content-type": "application/json",
            })
        except Exception as e:
            log.warning("adzuna[%s]: fetch failed: %s", q, e)
            continue
        for j in (data.get("results", []) if isinstance(data, dict) else []):
            if not isinstance(j, dict):
                continue
            jid = str(j.get("id", "")).strip()
            if not jid or jid in seen:
                continue
            title = j.get("title", "") or ""
            desc = strip_html(j.get("description", "") or "")
            if not matches_any(f"{title}\n{desc}", config.CATEGORY_KEYWORDS):
                continue
            seen.add(jid)
            loc = (j.get("location") or {}).get("display_name", "") \
                if isinstance(j.get("location"), dict) else ""
            company = (j.get("company") or {}).get("display_name", "") \
                if isinstance(j.get("company"), dict) else ""
            salary = ""
            if j.get("salary_min") and j.get("salary_max"):
                salary = f"{int(j['salary_min'])}-{int(j['salary_max'])}"
            out.append(Listing(
                source="adzuna",
                source_id=jid,
                stream=STREAM_JOB,
                title=title,
                company=company,
                description=clip(desc),
                tags=[loc] if loc else [],
                salary=salary,
                location=loc,
                url=j.get("redirect_url", "") or "",
                posted_at=j.get("created", "") or "",
            ))
        polite_sleep()

    log.info("adzuna: %d matched (%s)", len(out), country)
    return out
