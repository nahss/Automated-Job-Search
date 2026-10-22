"""Product Hunt -- official GraphQL API (Leads stream).

POST https://api.producthunt.com/v2/api/graphql
Header: Authorization: Bearer {PRODUCTHUNT_TOKEN}  (free developer token)

Pulls today's launches, filters to design-relevant topics, and normalizes
each into a lead Listing. The one-line outreach opener is drafted later in
scoring.py -- this module only gathers the raw lead.

Convention for the Leads tab: the product tagline is stored in tags[0] so
sheets.build_row can place it in the Tagline column without widening the
Listing dataclass.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from .. import config
from ..models import Listing, STREAM_LEAD
from ..util import clip, log, post_json, strip_html

_URL = "https://api.producthunt.com/v2/api/graphql"

_QUERY = """
query ($after: DateTime!, $before: DateTime!, $first: Int!) {
  posts(postedAfter: $after, postedBefore: $before, order: VOTES, first: $first) {
    edges {
      node {
        id
        name
        tagline
        description
        url
        website
        votesCount
        topics { edges { node { name slug } } }
        makers { name username twitterUsername url }
      }
    }
  }
}
"""


def _topic_slugs(node: dict) -> list[str]:
    out = []
    for edge in (((node.get("topics") or {}).get("edges")) or []):
        tnode = (edge or {}).get("node") or {}
        slug = tnode.get("slug") or tnode.get("name", "")
        if slug:
            out.append(str(slug).lower())
    return out


def _maker_bits(node: dict) -> tuple[str, str]:
    makers = node.get("makers") or []
    if not makers:
        return "", ""
    m = makers[0] or {}
    name = m.get("name", "") or m.get("username", "") or ""
    handle = m.get("twitterUsername") or m.get("username") or ""
    contact = f"@{handle}" if handle else (m.get("url", "") or "")
    return name, contact


def fetch() -> list[Listing]:
    token = os.environ.get("PRODUCTHUNT_TOKEN")
    if not token:
        log.warning("producthunt: PRODUCTHUNT_TOKEN not set; skipping leads")
        return []

    now = datetime.now(timezone.utc)
    after = (now - timedelta(days=1)).replace(microsecond=0)
    variables = {
        "after": after.isoformat(),
        "before": now.replace(microsecond=0).isoformat(),
        "first": config.FETCH_CAPS.get("producthunt", 50),
    }

    try:
        data = post_json(
            _URL,
            json_body={"query": _QUERY, "variables": variables},
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json",
                     "Accept": "application/json"},
        )
    except Exception as e:
        log.warning("producthunt: fetch failed: %s", e)
        return []

    if "errors" in data:
        log.warning("producthunt: API errors: %s", data.get("errors"))
        # fall through -- there may still be partial data

    edges = (((data.get("data") or {}).get("posts") or {}).get("edges")) or []
    allowed = set(config.PRODUCTHUNT_TOPICS)
    listings: list[Listing] = []

    for edge in edges:
        node = (edge or {}).get("node") or {}
        pid = str(node.get("id", "")).strip()
        if not pid:
            continue
        slugs = _topic_slugs(node)
        # Keep if it touches any design-relevant topic (or has no topics).
        if slugs and not (allowed & set(slugs)):
            continue
        maker, contact = _maker_bits(node)
        tagline = node.get("tagline", "") or ""
        listings.append(Listing(
            source="producthunt",
            source_id=pid,
            stream=STREAM_LEAD,
            title=node.get("name", "") or "",
            company=node.get("name", "") or "",
            description=clip(strip_html(node.get("description", "") or tagline)),
            tags=[tagline] + slugs,   # tags[0] = tagline (see module docstring)
            salary="",
            url=node.get("url", "") or "",
            posted_at="",
            maker=maker,
            contact=contact,
            website=node.get("website", "") or "",
        ))

    log.info("producthunt: %d leads (topic-filtered)", len(listings))
    return listings
