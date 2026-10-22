"""We Work Remotely -- per-category RSS feeds (parsed with feedparser).

WWR has no JSON API but publishes a public RSS feed per category.
Entry titles are usually "Company: Role".
"""

from __future__ import annotations

import feedparser

from .. import config
from ..models import Listing, STREAM_JOB
from ..util import clip, log, polite_sleep, strip_html

_FEEDS = [
    "https://weworkremotely.com/categories/remote-design-jobs.rss",
    "https://weworkremotely.com/categories/remote-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
    "https://weworkremotely.com/categories/remote-product-jobs.rss",
    "https://weworkremotely.com/categories/remote-qa-jobs.rss",
]


def _split_company_role(title: str) -> tuple[str, str]:
    """WWR titles look like 'Company: Role'. Split if we can."""
    if ":" in title:
        company, _, role = title.partition(":")
        return company.strip(), role.strip()
    return "", title.strip()


def fetch() -> list[Listing]:
    cap = config.FETCH_CAPS.get("weworkremotely", 60)
    listings: list[Listing] = []
    seen: set[str] = set()

    for url in _FEEDS:
        try:
            feed = feedparser.parse(url, agent=config.USER_AGENT)
        except Exception as e:
            log.warning("weworkremotely[%s]: parse failed: %s", url, e)
            continue
        if getattr(feed, "bozo", 0) and not feed.entries:
            log.warning("weworkremotely[%s]: no entries (%s)", url,
                        getattr(feed, "bozo_exception", ""))
            continue

        for entry in feed.entries:
            link = getattr(entry, "link", "") or ""
            jid = getattr(entry, "id", "") or link
            jid = str(jid).strip()
            if not jid or jid in seen:
                continue
            seen.add(jid)
            raw_title = getattr(entry, "title", "") or ""
            company, role = _split_company_role(raw_title)
            summary = getattr(entry, "summary", "") or ""
            listings.append(Listing(
                source="weworkremotely",
                source_id=jid,
                stream=STREAM_JOB,
                title=role or raw_title,
                company=company,
                description=clip(strip_html(summary)),
                tags=[t.get("term", "") for t in getattr(entry, "tags", [])
                      if isinstance(t, dict)],
                salary="",
                url=link,
                posted_at=getattr(entry, "published", "") or "",
            ))
            if len(listings) >= cap:
                break
        polite_sleep()
        if len(listings) >= cap:
            break

    log.info("weworkremotely: %d listings", len(listings))
    return listings
