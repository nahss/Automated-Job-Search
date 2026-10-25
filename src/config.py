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
# Score bands (see scoring.py): 85-100 strong fit, 65-84 clearly relevant,
# 40-64 tangential/partial, 0-39 not a fit. 45 (default) casts a wide net --
# it includes most partial fits; raise toward 65 for a tighter, cleaner list.
SCORE_THRESHOLD: int = _env_int("SCORE_THRESHOLD", 45)

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

# Seniority preference. The scoring prompt favors this level and meaningfully
# down-ranks clearly-senior / management roles (so many fall below the score
# threshold and drop out). For a harder cut, set EXCLUDE_SENIOR_TITLES=True to
# also drop senior-titled Jobs/Gigs outright. Founding / first-designer / 0->1
# roles are always kept, even if senior-titled.
SENIORITY_PREFERENCE: str = "mid-level / intermediate (the candidate has ~5 years)"
EXCLUDE_SENIOR_TITLES: bool = False
SENIOR_TITLE_KEYWORDS: list[str] = [
    "senior", "sr.", "sr ", "staff", "principal", "lead ", "lead,",
    "head of", "director", "vp ", "vice president", "chief", "distinguished",
    "manager", "mgr",
]

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
# COMPANY_BOARDS = verified (ats, slug) boards we fetch every run. Slug is the
# token from the careers URL:
#   greenhouse      -> boards.greenhouse.io/<slug>
#   lever           -> jobs.lever.co/<slug>            (case matters)
#   ashby           -> jobs.ashbyhq.com/<slug>
#   smartrecruiters -> jobs.smartrecruiters.com/<slug>
# The discovery resolver (src/discovery.py) grows this list automatically by
# resolving SEED_COMPANIES and companies seen in the Leads / HN streams,
# persisting hits to the "_Watchlist" sheet tab.
COMPANY_BOARDS: list[tuple[str, str]] = [
    # --- Ashby (highest-yield for post-2018 startups; smallest teams first) ---
    ("ashby", "substack"),
    ("ashby", "payabli"),        # fintech
    ("ashby", "posthog"),        # dev tools
    ("ashby", "linear"),         # very design-led
    ("ashby", "modal"),          # AI infra
    ("ashby", "clerk"),          # dev tools
    ("ashby", "mercury"),        # fintech
    ("ashby", "ramp"),           # fintech
    ("ashby", "deel"),
    ("ashby", "mazehq"),         # design/research tool
    ("ashby", "monarchmoney"),   # fintech
    ("ashby", "rula"),           # health
    ("ashby", "secureframe"),
    ("ashby", "docker"),
    ("ashby", "supabase"),
    ("ashby", "replit"),
    ("ashby", "claylabs"),       # Clay (GTM) -- verified via live postings
    # --- Greenhouse (verified against live runs) ---
    ("greenhouse", "stripe"),
    ("greenhouse", "coinbase"),
    ("greenhouse", "figma"),
    ("greenhouse", "vercel"),
    ("greenhouse", "databricks"),
    ("greenhouse", "gusto"),
    # --- Lever (slugs from live postings; case-sensitive) ---
    ("lever", "appzen"),         # AI + finance
    ("lever", "payjoy"),         # fintech
    ("lever", "UpMetrics"),
    ("lever", "shakacode"),
    ("lever", "pioneer-services"),  # Web3 / fintech
    # --- SmartRecruiters ---
    ("smartrecruiters", "Wix2"),           # Wix
    ("smartrecruiters", "Xplor"),          # payments
    ("smartrecruiters", "PublicisGroupe"),
]

# SEED_COMPANIES = candidate slugs the resolver looks up (Ashby-first), keeps
# only if the board exists (and, with the size filter, if the team is small).
# NOT pre-verified -- the resolver drops ones with no public board and caches
# the result so it isn't retried every run.
SEED_COMPANIES: list[str] = [
    # AI (seed / Series A)
    "decagon", "harvey", "lovable", "cartesia", "dust", "vapi",
    "chroma", "variance", "naceai", "radicalai", "salient",
    "consensus", "antimetal", "rove", "ando",
    # Dev tools
    "resend", "dub", "cal.com", "mintlify", "liveblocks",
    "graphite", "raycast", "browserbase", "attio",
    # Fintech
    "column", "lithic", "highnote", "moderntreasury", "mesh",
]

# --------------------------------------------------------------------------
# Company-size tagging + small-team filter
# --------------------------------------------------------------------------
# A board's total open-role count is a proxy for company size. Small teams are
# the strongest "they need a designer" signal.
COMPANY_SIZE_SMALL_MAX: int = 15      # <= this many open roles  -> "small"
COMPANY_SIZE_MID_MAX: int = 60        # <= this many             -> "mid"  (else "large")
# Large companies: True drops them entirely, False keeps them flagged (the
# scoring prompt down-ranks large teams either way).
DROP_LARGE_COMPANIES: bool = False

# --------------------------------------------------------------------------
# Pitch tab: companies with a LIVE board but NO matching design role -- i.e.
# they're hiring (and growing) yet have no designer. Strongest cold-pitch
# signal. Each gets an AI-drafted opener in your voice.
# --------------------------------------------------------------------------
PITCH_ENABLED: bool = True
# Only pitch companies whose team size (open-role count) is one of these.
PITCH_SIZES: set[str] = {"small", "mid"}
# Max new pitch companies to draft openers for per run (bounds LLM spend).
MAX_PITCH_PER_RUN: int = 40

# --------------------------------------------------------------------------
# Discovery resolver
# --------------------------------------------------------------------------
DISCOVERY_ENABLED: bool = True
# Resolve company names seen in these streams, not just SEED_COMPANIES.
DISCOVER_FROM_LEADS: bool = True      # Product Hunt / Show HN
DISCOVER_FROM_HACKERNEWS: bool = True # "Who is hiring" company names (fuzzy)
# Hard cap on resolver lookups per run (each cached afterwards, hit or miss).
RESOLVER_MAX_PER_RUN: int = 40
# Sheet tab that persists the resolver cache + discovered boards.
WATCHLIST_TAB: str = "_Watchlist"

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
