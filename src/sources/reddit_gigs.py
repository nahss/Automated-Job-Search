"""Reddit gigs -- keyless JSON path (no OAuth approval wait).

GET https://www.reddit.com/r/{sub}/new.json?limit=50 with a descriptive
User-Agent. ~10 req/min is plenty for a once-daily run.

Filtering:
  * r/forhire -> keep only [Hiring] posts (skip [For Hire] self-promo).
  * every sub -> keep posts matching David's categories OR the pitch-deck
    keyword list.

Upgrade path: swap this module's `_fetch_sub` for a PRAW + OAuth call later;
the env vars (REDDIT_CLIENT_ID/SECRET/USER_AGENT) are already reserved. The
keyless path is fine for v1.
"""

from __future__ import annotations

from .. import config
from ..models import Listing, STREAM_GIG
from ..util import (clip, get_json, log, matches_any, polite_sleep,
                    strip_html, to_iso)

_URL = "https://www.reddit.com/r/{}/new.json"


def _keep(sub: str, title: str, body: str, flair: str) -> bool:
    text = f"{title}\n{body}\n{flair}"
    low_title = title.lower()
    low_flair = (flair or "").lower()

    if sub.lower() == "forhire":
        # Keep only people HIRING, not those offering ("[For Hire]").
        is_hiring = ("[hiring]" in low_title) or ("hiring" in low_flair
                                                  and "for hire" not in low_flair)
        if not is_hiring:
            return False

    return (matches_any(text, config.CATEGORY_KEYWORDS)
            or matches_any(text, config.PITCH_DECK_KEYWORDS))


def _fetch_sub(sub: str) -> list[Listing]:
    limit = config.FETCH_CAPS.get("reddit_gigs", 50)
    try:
        data = get_json(_URL.format(sub), params={"limit": limit},
                        headers={"Accept": "application/json"})
    except Exception as e:
        log.warning("reddit[%s]: fetch failed: %s", sub, e)
        return []

    children = (data.get("data", {}) or {}).get("children", []) \
        if isinstance(data, dict) else []
    out: list[Listing] = []
    for child in children or []:
        post = (child or {}).get("data", {})
        if not isinstance(post, dict):
            continue
        title = post.get("title", "") or ""
        body = strip_html(post.get("selftext", "") or "")
        flair = post.get("link_flair_text", "") or ""
        if not _keep(sub, title, body, flair):
            continue
        pid = str(post.get("id", "")).strip()
        if not pid:
            continue
        permalink = post.get("permalink", "")
        url = f"https://www.reddit.com{permalink}" if permalink \
            else post.get("url", "")
        out.append(Listing(
            source=f"reddit:{sub}",
            source_id=pid,
            stream=STREAM_GIG,
            title=title,
            company=f"u/{post.get('author', '')}",
            description=clip(body),
            tags=[flair] if flair else [],
            salary="",  # budget, if any, lives in the post body
            url=url,
            posted_at=to_iso(post.get("created_utc", "")),
        ))
    return out


def fetch() -> list[Listing]:
    listings: list[Listing] = []
    for sub in config.SUBREDDITS:
        listings.extend(_fetch_sub(sub))
        polite_sleep()
    log.info("reddit_gigs: %d matched posts across %d subs",
             len(listings), len(config.SUBREDDITS))
    return listings
