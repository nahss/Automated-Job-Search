"""Hacker News "Who is hiring" -- free, keyless Algolia API.

1. Find the latest monthly thread: search stories by the `whoishiring`
   account whose title contains "Who is hiring".
2. Fetch that story's comments.
3. Keep top-level comments that keyword-match David's categories.

Each surviving comment becomes a Listing (stream=job). Company/role are not
cleanly structured in HN posts, so the whole comment text is the scoring
context and the LLM judges fit.
"""

from __future__ import annotations

from .. import config
from ..models import Listing, STREAM_JOB
from ..util import clip, get_json, log, matches_any, strip_html, to_iso

_SEARCH = "https://hn.algolia.com/api/v1/search"
_ITEM = "https://hn.algolia.com/api/v1/items/{}"


def _latest_story_id() -> str | None:
    try:
        data = get_json(_SEARCH, params={
            "query": "Ask HN: Who is hiring",
            "tags": "story",
        })
    except Exception as e:
        log.warning("hackernews: story search failed: %s", e)
        return None

    best_id, best_ts = None, -1
    for hit in data.get("hits", []) or []:
        author = (hit.get("author") or "").lower()
        title = (hit.get("title") or "").lower()
        if author != "whoishiring":
            continue
        if "who is hiring" not in title:
            continue  # skip "freelancer" / "wants to be hired" threads
        ts = int(hit.get("created_at_i") or 0)
        if ts > best_ts:
            best_ts, best_id = ts, str(hit.get("objectID"))
    return best_id


def _first_line(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line
    return text[:120]


def fetch() -> list[Listing]:
    story_id = _latest_story_id()
    if not story_id:
        log.warning("hackernews: no 'Who is hiring' thread found")
        return []

    try:
        story = get_json(_ITEM.format(story_id))
    except Exception as e:
        log.warning("hackernews: item fetch failed: %s", e)
        return []

    cap = config.FETCH_CAPS.get("hackernews", 80)
    listings: list[Listing] = []
    for child in story.get("children", []) or []:
        if not isinstance(child, dict):
            continue
        raw = child.get("text") or ""
        if not raw:
            continue
        text = strip_html(raw)
        # Cheap pre-filter: only keep comments mentioning David's categories.
        if not matches_any(text, config.CATEGORY_KEYWORDS):
            continue
        cid = str(child.get("id", "")).strip()
        if not cid:
            continue
        listings.append(Listing(
            source="hackernews",
            source_id=cid,
            stream=STREAM_JOB,
            title=_first_line(text),
            company=child.get("author", "") or "",
            description=clip(text),
            tags=["who-is-hiring"],
            salary="",
            url=f"https://news.ycombinator.com/item?id={cid}",
            posted_at=to_iso(child.get("created_at", "")),
        ))
        if len(listings) >= cap:
            break

    log.info("hackernews: %d keyword-matched listings (story %s)",
             len(listings), story_id)
    return listings
