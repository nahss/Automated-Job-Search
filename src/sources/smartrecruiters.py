"""SmartRecruiters postings -- direct from company career pages (keyless JSON).

GET https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100

The postings list doesn't include the full job description (that needs a
per-posting detail call), so we keyword-match and score on the title,
department, function and location -- enough to judge design-role fit without
a second request per posting.
"""

from __future__ import annotations

from .. import boards, config
from ..models import Listing, STREAM_JOB
from ..util import clip, company_size, get_json, log, matches_any, polite_sleep, to_iso

_URL = "https://api.smartrecruiters.com/v1/companies/{slug}/postings"


def _companies() -> list[dict]:
    return boards.boards_for("smartrecruiters")


def _label(obj) -> str:
    if isinstance(obj, dict):
        return obj.get("label", "") or obj.get("name", "") or ""
    return str(obj or "")


def _location(loc) -> str:
    if not isinstance(loc, dict):
        return ""
    bits = [loc.get("city", ""), loc.get("region", ""), loc.get("country", "")]
    text = ", ".join(b for b in bits if b)
    if loc.get("remote"):
        text = (text + " (remote)").strip()
    return text


def fetch() -> list[Listing]:
    cap = config.FETCH_CAPS.get("smartrecruiters", 100)
    out: list[Listing] = []
    companies = _companies()

    for c in companies:
        slug = str(c.get("slug", "")).strip()
        if not slug:
            continue
        name = c.get("name") or slug
        try:
            data = get_json(_URL.format(slug=slug), params={"limit": cap})
        except Exception as e:
            log.warning("smartrecruiters[%s]: fetch failed: %s", slug, e)
            polite_sleep()
            continue

        postings = data.get("content", []) if isinstance(data, dict) else []
        # totalFound is the true board size (postings here are one page).
        total = data.get("totalFound") if isinstance(data, dict) else None
        if not isinstance(total, int):
            total = len(postings)
        size = company_size(total)
        kept = 0
        for p in postings:
            if not isinstance(p, dict):
                continue
            title = p.get("name", "") or ""
            dept = _label(p.get("department"))
            func = _label(p.get("function"))
            loc = _location(p.get("location"))
            if not matches_any(f"{title}\n{dept}\n{func}", config.CATEGORY_KEYWORDS):
                continue
            jid = str(p.get("id") or p.get("refNumber") or "").strip()
            if not jid:
                continue
            actions = p.get("actions") if isinstance(p.get("actions"), dict) else {}
            url = actions.get("details") or actions.get("apply") \
                or f"https://jobs.smartrecruiters.com/{slug}/{jid}"
            desc_bits = [b for b in (dept, func, loc,
                                     _label(p.get("typeOfEmployment"))) if b]
            out.append(Listing(
                source=f"smartrecruiters:{slug}",
                source_id=jid,
                stream=STREAM_JOB,
                title=title,
                company=_label(p.get("company")) or name,
                description=clip(" · ".join(desc_bits)),
                tags=[t for t in (loc, dept) if t],
                salary="",
                url=url,
                posted_at=to_iso(p.get("releasedDate", "")),
                company_size=size,
                open_roles=total,
            ))
            kept += 1
        if kept:
            log.info("smartrecruiters[%s]: %d matched", slug, kept)
        polite_sleep()

    log.info("smartrecruiters: %d matched across %d companies",
             len(out), len(companies))
    return out
