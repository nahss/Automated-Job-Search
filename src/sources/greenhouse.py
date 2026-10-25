"""Greenhouse job boards -- direct from company career pages (keyless JSON).

GET https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true

Iterates the Greenhouse companies in config.TARGET_COMPANIES, keeps only
postings matching David's target categories, and normalizes them into the
Jobs stream. A company whose slug is wrong/changed just 404s -> warn + skip.
"""

from __future__ import annotations

import html

from .. import boards, config
from ..models import Listing, STREAM_JOB
from ..util import (clip, company_size, get_json, log, matches_any,
                    polite_sleep, strip_html, to_iso)

_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"


def _companies() -> list[dict]:
    return boards.boards_for("greenhouse")


def fetch() -> list[Listing]:
    cap = config.FETCH_CAPS.get("greenhouse", 60)
    out: list[Listing] = []
    companies = _companies()

    for c in companies:
        slug = str(c.get("slug", "")).strip()
        if not slug:
            continue
        name = c.get("name") or slug
        try:
            data = get_json(_URL.format(slug=slug), params={"content": "true"})
        except Exception as e:
            log.warning("greenhouse[%s]: fetch failed: %s", slug, e)
            boards.record_stat("greenhouse", slug, name, False, 0, 0, [])
            polite_sleep()
            continue

        jobs = data.get("jobs", []) if isinstance(data, dict) else []
        total = len(jobs)                      # board's total open roles
        size = company_size(total)
        kept = 0
        titles: list[str] = []
        for j in jobs[:cap]:
            if not isinstance(j, dict):
                continue
            title = j.get("title", "") or ""
            if len(titles) < 6:
                titles.append(title)
            # Greenhouse `content` is HTML-entity-encoded HTML.
            content = strip_html(html.unescape(j.get("content", "") or ""))
            if not matches_any(f"{title}\n{content}", config.CATEGORY_KEYWORDS):
                continue
            jid = str(j.get("id") or j.get("absolute_url") or "").strip()
            if not jid:
                continue
            loc = (j.get("location") or {}).get("name", "") if isinstance(
                j.get("location"), dict) else ""
            out.append(Listing(
                source=f"greenhouse:{slug}",
                source_id=jid,
                stream=STREAM_JOB,
                title=title,
                company=name,
                description=clip(content),
                tags=[loc] if loc else [],
                salary="",
                url=j.get("absolute_url", "") or "",
                posted_at=to_iso(j.get("updated_at", "")),
                company_size=size,
                open_roles=total,
            ))
            kept += 1
        boards.record_stat("greenhouse", slug, name, True, total, kept, titles)
        if kept:
            log.info("greenhouse[%s]: %d matched", slug, kept)
        polite_sleep()

    log.info("greenhouse: %d matched across %d companies", len(out), len(companies))
    return out
