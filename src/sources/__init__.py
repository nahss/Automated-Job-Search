"""Source adapters.

Every module here exposes `fetch() -> list[Listing]`. This package exposes a
`REGISTRY` mapping the config toggle name -> that fetch callable, so `main`
can iterate sources generically. Adding a source = drop a module in here and
add one line to REGISTRY (plus a toggle in config.SOURCES_ENABLED).
"""

from __future__ import annotations

from typing import Callable

from ..models import Listing

from . import (
    adzuna,
    arbeitnow,
    ashby,
    greenhouse,
    hackernews,
    himalayas,
    jobicy,
    jooble,
    lever,
    producthunt,
    reddit_gigs,
    remoteok,
    remotive,
    showhn,
    smartrecruiters,
    themuse,
    weworkremotely,
    working_nomads,
)

REGISTRY: dict[str, Callable[[], list[Listing]]] = {
    "remotive": remotive.fetch,
    "remoteok": remoteok.fetch,
    "arbeitnow": arbeitnow.fetch,
    "jobicy": jobicy.fetch,
    "himalayas": himalayas.fetch,
    "working_nomads": working_nomads.fetch,
    "weworkremotely": weworkremotely.fetch,
    "hackernews": hackernews.fetch,
    "themuse": themuse.fetch,
    "adzuna": adzuna.fetch,
    "jooble": jooble.fetch,
    # company career pages (ATS)
    "greenhouse": greenhouse.fetch,
    "lever": lever.fetch,
    "ashby": ashby.fetch,
    "smartrecruiters": smartrecruiters.fetch,
    "reddit_gigs": reddit_gigs.fetch,
    "producthunt": producthunt.fetch,
    "showhn": showhn.fetch,
}
