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

# Freshness: only keep listings posted within this many days. The job-board
# and ATS APIs already serve only currently-OPEN roles; this adds a recency
# cap on top. Set to 0 to disable (keep anything open regardless of age).
MAX_POSTING_AGE_DAYS: int = _env_int("MAX_POSTING_AGE_DAYS", 7)

# Some sources don't expose a reliable posted date. Keep those (False) so a
# good role isn't dropped over missing metadata, or require a date (True).
DROP_UNDATED_LISTINGS: bool = False

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
    # jobs -- direct from company career pages (ATS APIs); see TARGET_COMPANIES
    "greenhouse": True,
    "lever": True,
    "ashby": True,
    "smartrecruiters": True,
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
    "greenhouse": 60,      # per company (max postings scanned)
    "lever": 60,           # per company
    "ashby": 60,           # per company
    "smartrecruiters": 100,  # per company
    "reddit_gigs": 50,     # per subreddit
    "producthunt": 50,
    "showhn": 30,
}

# Remotive asks integrators to be gentle; hard cap on calls per run.
REMOTIVE_MAX_CALLS: int = 4

# Upper bound on how many NEW listings we send to the LLM per run, per
# stream. Protects against a surprise flood of new postings blowing up cost.
# Listings are interleaved across sources before this cap is applied, so no
# single source (e.g. a big job board) can starve the others of slots.
# At Haiku prices this is ~$0.0002/listing, so 300 is only a few cents/run.
MAX_SCORED_PER_STREAM: int = 300

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
# Company career pages, tracked directly via their ATS (applicant tracking
# system) public JSON APIs -- the same data that powers each company's own
# "Careers" page, but clean and keyless. Roles are keyword-filtered to your
# target categories before scoring, then land in the Jobs tab like any other.
#
# Each entry: the ATS platform + the company's "slug" (from its careers URL):
#   greenhouse      -> boards.greenhouse.io/<slug>        e.g. .../stripe
#   lever           -> jobs.lever.co/<slug>               e.g. .../mozilla
#   ashby           -> jobs.ashbyhq.com/<slug> (or <slug>.ashbyhq.com)
#   smartrecruiters -> jobs.smartrecruiters.com/<slug>    e.g. .../Square
# To find a company's slug, open its careers page and read the URL. (The
# SmartRecruiters adapter is enabled and ready -- add companies here in the
# same format; none are seeded because its identifiers are less predictable.)
#
# NOTE: this starter list is best-effort -- a company can switch ATS or use a
# different slug, in which case that entry simply returns nothing (the run
# logs a warning and moves on). Verify/replace with the companies YOU want;
# the per-company counts in the logs show which ones are producing results.
TARGET_COMPANIES: list[dict] = [
    # fintech / payments
    {"ats": "greenhouse", "slug": "stripe", "name": "Stripe"},
    {"ats": "greenhouse", "slug": "coinbase", "name": "Coinbase"},
    {"ats": "ashby", "slug": "ramp", "name": "Ramp"},
    # SaaS / product / dev tools (design-heavy orgs)
    {"ats": "greenhouse", "slug": "figma", "name": "Figma"},
    {"ats": "greenhouse", "slug": "vercel", "name": "Vercel"},
    {"ats": "ashby", "slug": "linear", "name": "Linear"},
    {"ats": "ashby", "slug": "posthog", "name": "PostHog"},
    {"ats": "ashby", "slug": "replit", "name": "Replit"},
    # larger SaaS
    {"ats": "greenhouse", "slug": "databricks", "name": "Databricks"},
    {"ats": "greenhouse", "slug": "gusto", "name": "Gusto"},
    # NOTE: slugs verified against a live run (2026-10-23). Removed ones that
    # 404'd: greenhouse/plaid, greenhouse/notion, lever/mozilla. Add your own
    # here -- Lever and SmartRecruiters currently have no seeds.
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
