"""Lever postings -- direct from company career pages (keyless JSON).

GET https://api.lever.co/v0/postings/{slug}?mode=json

Returns a flat JSON array of postings. Iterates the Lever companies in
config.TARGET_COMPANIES, keyword-filters, and normalizes into the Jobs stream.
"""

from __future__ import annotations

from .. import config
from ..models import Listing, STREAM_JOB
from ..util import (clip, get_json, log, matches_any, polite_sleep,
                    strip_html, to_iso)

_URL = "https://api.lever.co/v0/postings/{slug}"


def _companies() -> list[dict]:
    return [c for c in config.TARGET_COMPANIES if c.get("ats") == "lever"]


def _posted(created_ms) -> str:
    # Lever `createdAt` is a millisecond epoch; to_iso expects seconds.
    try:
        return to_iso(float(created_ms) / 1000.0) if created_ms else ""
    except (TypeError, ValueError):
        return ""


def fetch() -> list[Listing]:
    cap = config.FETCH_CAPS.get("lever", 60)
    out: list[Listing] = []
    companies = _companies()

    for c in companies:
        slug = str(c.get("slug", "")).strip()
        if not slug:
            continue
        name = c.get("name") or slug
        try:
            data = get_json(_URL.format(slug=slug), params={"mode": "json"})
        except Exception as e:
            log.warning("lever[%s]: fetch failed: %s", slug, e)
            polite_sleep()
            continue

        postings = data if isinstance(data, list) else []
        kept = 0
        for p in postings[:cap]:
            if not isinstance(p, dict):
                continue
            title = p.get("text", "") or ""
            cats = p.get("categories") or {}
            team = cats.get("team", "") if isinstance(cats, dict) else ""
            loc = cats.get("location", "") if isinstance(cats, dict) else ""
            commitment = cats.get("commitment", "") if isinstance(cats, dict) else ""
            desc = p.get("descriptionPlain") or strip_html(p.get("description", "") or "")
            if not matches_any(f"{title}\n{team}\n{desc}", config.CATEGORY_KEYWORDS):
                continue
            jid = str(p.get("id", "")).strip()
            if not jid:
                continue
            tags = [t for t in (loc, team, commitment) if t]
            out.append(Listing(
                source=f"lever:{slug}",
                source_id=jid,
                stream=STREAM_JOB,
                title=title,
                company=name,
                description=clip(desc),
                tags=tags,
                salary="",
                url=p.get("hostedUrl", "") or p.get("applyUrl", "") or "",
                posted_at=_posted(p.get("createdAt")),
            ))
            kept += 1
        if kept:
            log.info("lever[%s]: %d matched", slug, kept)
        polite_sleep()

    log.info("lever: %d matched across %d companies", len(out), len(companies))
    return out
