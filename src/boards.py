"""The company-board watchlist: static config + runtime-discovered boards.

`COMPANY_BOARDS` in config is the fixed, verified set. The discovery resolver
adds more at runtime (persisted in the "_Watchlist" sheet tab); main injects
those via `set_discovered()` before the ATS adapters run. The adapters call
`boards_for(ats)` and don't care where a board came from.
"""

from __future__ import annotations

from . import config

# Runtime-injected discovered boards: list of {"ats","slug","name","source"}.
_discovered: list[dict] = []


def set_discovered(boards: list[dict]) -> None:
    global _discovered
    _discovered = list(boards or [])


def _static() -> list[dict]:
    out = []
    for ats, slug in config.COMPANY_BOARDS:
        out.append({"ats": ats, "slug": slug, "name": slug, "source": "config"})
    return out


def all_boards() -> list[dict]:
    """Static + discovered, deduped by (ats, slug), config taking precedence."""
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for b in _static() + _discovered:
        key = (b["ats"], b["slug"])
        if key in seen:
            continue
        seen.add(key)
        out.append(b)
    return out


def boards_for(ats: str) -> list[dict]:
    return [b for b in all_boards() if b["ats"] == ats]
