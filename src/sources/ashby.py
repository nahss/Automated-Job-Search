"""Ashby job boards -- direct from company career pages (keyless JSON).

GET https://api.ashbyhq.com/posting-api/job-board/{slug}

Iterates the Ashby companies in config.TARGET_COMPANIES, keyword-filters, and
normalizes into the Jobs stream.
"""

from __future__ import annotations

from .. import config
from ..models import Listing, STREAM_JOB
from ..util import (clip, get_json, log, matches_any, polite_sleep,
                    strip_html, to_iso)

_URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}"


def _companies() -> list[dict]:
    return [c for c in config.TARGET_COMPANIES if c.get("ats") == "ashby"]


def fetch() -> list[Listing]:
    cap = config.FETCH_CAPS.get("ashby", 60)
    out: list[Listing] = []
    companies = _companies()

    for c in companies:
        slug = str(c.get("slug", "")).strip()
        if not slug:
            continue
        name = c.get("name") or slug
        try:
            data = get_json(_URL.format(slug=slug),
                            params={"includeCompensation": "true"})
        except Exception as e:
            log.warning("ashby[%s]: fetch failed: %s", slug, e)
            polite_sleep()
            continue

        jobs = data.get("jobs", []) if isinstance(data, dict) else []
        kept = 0
        for j in jobs[:cap]:
            if not isinstance(j, dict):
                continue
            title = j.get("title", "") or ""
            dept = j.get("department", "") or ""
            team = j.get("team", "") or ""
            desc = j.get("descriptionPlain") or strip_html(
                j.get("descriptionHtml", "") or "")
            if not matches_any(f"{title}\n{dept}\n{team}\n{desc}",
                               config.CATEGORY_KEYWORDS):
                continue
            jid = str(j.get("id", "")).strip()
            if not jid:
                continue
            loc = j.get("location", "") or ""
            tags = [t for t in (loc, dept) if t]
            if j.get("isRemote"):
                tags.append("remote")
            out.append(Listing(
                source=f"ashby:{slug}",
                source_id=jid,
                stream=STREAM_JOB,
                title=title,
                company=name,
                description=clip(desc),
                tags=tags,
                salary="",
                url=j.get("jobUrl", "") or j.get("applyUrl", "") or "",
                posted_at=to_iso(j.get("publishedAt", "")),
            ))
            kept += 1
        if kept:
            log.info("ashby[%s]: %d matched", slug, kept)
        polite_sleep()

    log.info("ashby: %d matched across %d companies", len(out), len(companies))
    return out
