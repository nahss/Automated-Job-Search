"""All the knobs a non-coder would reasonably want to tune live here.

Anything secret (API keys, the sheet id, the service-account JSON) is read
from the environment, never stored here. Everything else -- toggles,
thresholds, keyword lists, caps -- is a plain Python value you can edit.
"""

from __future__ import annotations

import os


def _env(name: str, default: str) -> str:
    """Env var value, treating a missing OR blank value as the default.

    GitHub Actions sets every optional secret referenced in a workflow's
    `env:` block to an EMPTY STRING when the secret isn't defined. A bare
    os.environ.get(name, default) would then return "" (not the default),
    so int("")/model calls break. This collapses missing and blank to one
    thing: fall back to the default.
    """
    val = os.environ.get(name)
    return default if val is None or val.strip() == "" else val.strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------

# Only listings scoring >= this land in the sheet. Env override wins so you
# can tweak it from GitHub Actions without a code change.
SCORE_THRESHOLD: int = _env_int("SCORE_THRESHOLD", 65)

# LLM provider + model, both swappable via env.
#   LLM_PROVIDER: "anthropic" (default) or "openai"
#   LLM_MODEL:    a CURRENT fast, low-cost model string (Haiku-class).
LLM_PROVIDER: str = _env("LLM_PROVIDER", "anthropic").lower()
LLM_MODEL: str = _env("LLM_MODEL", "claude-haiku-5-5")

# --------------------------------------------------------------------------
# Timezone (dates written into the sheet)
# --------------------------------------------------------------------------
TIMEZONE: str = _env("TIMEZONE", "Africa/Lagos")  # UTC+1

# --------------------------------------------------------------------------
# Source on/off toggles. Flip any to False to skip that source entirely.
# --------------------------------------------------------------------------
SOURCES_ENABLED: dict[str, bool] = {
    # jobs
    "remotive": True,
    "remoteok": True,
    "arbeitnow": True,
    "jobicy": True,
    "himalayas": True,
    "working_nomads": True,
    "weworkremotely": True,
    "hackernews": True,
    # gigs
    "reddit_gigs": True,
    # leads
    "producthunt": True,
    "showhn": False,  # optional extra lead source; off by default
}

# --------------------------------------------------------------------------
# Per-source fetch caps (how many raw items to pull before scoring).
# Keeps LLM spend and API load bounded.
# --------------------------------------------------------------------------
FETCH_CAPS: dict[str, int] = {
    "remotive": 100,
    "remoteok": 100,
    "arbeitnow": 100,
    "jobicy": 50,
    "himalayas": 50,
    "working_nomads": 50,
    "weworkremotely": 60,
    "hackernews": 80,
    "reddit_gigs": 50,     # per subreddit
    "producthunt": 50,
    "showhn": 30,
}

# Remotive asks integrators to be gentle; hard cap on calls per run.
REMOTIVE_MAX_CALLS: int = 4

# Upper bound on how many NEW listings we send to the LLM per run, per
# stream. Protects against a surprise flood of new postings blowing up cost.
MAX_SCORED_PER_STREAM: int = 120

# --------------------------------------------------------------------------
# Reddit (keyless path by default)
# --------------------------------------------------------------------------
SUBREDDITS: list[str] = [
    "forhire",
    "jobbit",
    "DesignJobs",
    "UI_Design",
    "graphic_design",
    "startups",
    "hiring",
]

# --------------------------------------------------------------------------
# Product Hunt topics we consider design-relevant.
# --------------------------------------------------------------------------
PRODUCTHUNT_TOPICS: list[str] = [
    "artificial-intelligence",
    "developer-tools",
    "saas",
    "fintech",
    "design-tools",
    "productivity",
]

# --------------------------------------------------------------------------
# Keyword lists (lowercased matching).
# --------------------------------------------------------------------------

# Pitch-deck / presentation gigs -> routed to the Gigs tab.
PITCH_DECK_KEYWORDS: list[str] = [
    "pitch deck",
    "investor deck",
    "pitch-deck",
    "deck design",
    "presentation design",
    "slide deck",
    "keynote",
    "deck designer",
]

# David's five target role categories, expanded into match terms. Used as a
# cheap pre-filter for noisy sources (Reddit, HN) before spending LLM calls.
CATEGORY_KEYWORDS: list[str] = [
    # product / UX design
    "product design", "product designer", "ui/ux", "ui ux", "ux design",
    "ux designer", "ui design", "ui designer", "interaction design",
    "visual design", "web design", "design engineer", "designer",
    "figma", "prototyping", "wireframe",
    # AI / agentic product
    "ai product", "ai-first", "agentic", "ai prototyp", "ai design",
    "generative", "llm", "prompt",
    # pitch deck (also in PITCH_DECK_KEYWORDS)
    "pitch deck", "presentation design", "slide deck",
    # QA
    "quality assurance", "qa engineer", "qa tester", "quality analyst",
    "test engineer",
]

# How many chars of a description to keep when sending to the LLM.
DESCRIPTION_MAX_CHARS: int = 2000

# Small politeness delay (seconds) between external calls.
REQUEST_DELAY_SECONDS: float = _env_float("REQUEST_DELAY_SECONDS", 0.5)

# Descriptive User-Agent for sources that require/appreciate one.
USER_AGENT: str = _env(
    "USER_AGENT",
    "AutomatedJobSearch/1.0 (+https://github.com/nahss; daily job & lead agent)",
)
