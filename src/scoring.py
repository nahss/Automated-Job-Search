"""LLM scoring + lead-opener drafting.

Two jobs:
  * score_listing()  -> (score 0-100, one-line reason) for jobs & gigs.
  * draft_lead()     -> (fit 0-100, one-line opener) for leads.

The LLM provider/model are behind env vars (config.LLM_PROVIDER / LLM_MODEL)
so they are swappable. Anthropic is the default. Output parsing is
defensive: malformed JSON is skipped and logged, never fatal.

All prompts live here so they are easy to tweak.
"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

from . import config
from .models import Listing
from .util import log

# Set to the message of the most recent LLM transport/API error (e.g. an
# out-of-credits 400). main.py reads this to surface the real cause when a
# run's scoring fails wholesale.
LAST_ERROR: str = ""

# --------------------------------------------------------------------------
# CV loading
# --------------------------------------------------------------------------

_CV_PATH = Path(__file__).resolve().parent.parent / "data" / "cv.md"

# Fallback used only if data/cv.md is missing/empty (a warning is logged).
_PROFILE_FALLBACK = """\
David Ayewah — Product Designer & UI/UX Designer, ~5 years, based in Lagos,
Nigeria, open to remote/global roles.
Domains: fintech, SaaS, logistics/fleet, health tech, e-commerce, Web3/crypto.
Also does pitch-deck / presentation design, leaning into AI/agentic product work.
Core skills: Figma, Adobe Illustrator, ProtoPie, Framer, motion graphics, 3D
mockups, AI-assisted design, AI prototyping, AI-assisted front-end (React/JS at
a working-with-AI level, not deep engineering).
Target role categories (all count as relevant): (1) Product design,
(2) UI/UX design, (3) Agentic / AI software product work, (4) Pitch-deck /
presentation design, (5) Quality assurance (QA)."""


@lru_cache(maxsize=1)
def load_cv() -> str:
    try:
        text = _CV_PATH.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        log.warning("data/cv.md not found; using built-in profile summary")
        return _PROFILE_FALLBACK
    # Treat a file that is only the template boilerplate as effectively empty.
    meaningful = text.replace("#", "").replace(">", "").strip()
    if len(meaningful) < 80:
        log.warning("data/cv.md looks empty; using built-in profile summary")
        return _PROFILE_FALLBACK
    return text


# --------------------------------------------------------------------------
# Provider-agnostic completion
# --------------------------------------------------------------------------

class LLMUnavailable(RuntimeError):
    """Raised when no LLM credentials/provider are configured."""


def llm_available() -> bool:
    if config.LLM_PROVIDER == "anthropic":
        return bool(os.environ.get("ANTHROPIC_API_KEY"))
    if config.LLM_PROVIDER == "openai":
        return bool(os.environ.get("OPENAI_API_KEY"))
    return False


def _complete(system: str, user: str, max_tokens: int = 400) -> str:
    """Return the model's text output. Raises on provider/transport error."""
    provider = config.LLM_PROVIDER
    if provider == "anthropic":
        return _complete_anthropic(system, user, max_tokens)
    if provider == "openai":
        return _complete_openai(system, user, max_tokens)
    raise LLMUnavailable(f"unknown LLM_PROVIDER: {provider!r}")


@lru_cache(maxsize=1)
def _anthropic_client():
    from anthropic import Anthropic  # imported lazily so dry-run w/o key works
    return Anthropic()  # reads ANTHROPIC_API_KEY from env


def _complete_anthropic(system: str, user: str, max_tokens: int) -> str:
    client = _anthropic_client()
    # NOTE: do NOT send temperature/top_p/top_k -- current models reject them.
    msg = client.messages.create(
        model=config.LLM_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(
        block.text for block in msg.content
        if getattr(block, "type", None) == "text"
    )


@lru_cache(maxsize=1)
def _openai_client():
    from openai import OpenAI
    return OpenAI()  # reads OPENAI_API_KEY from env


def _complete_openai(system: str, user: str, max_tokens: int) -> str:
    client = _openai_client()
    resp = client.chat.completions.create(
        model=config.LLM_MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
    )
    return resp.choices[0].message.content or ""


# --------------------------------------------------------------------------
# JSON extraction (defensive)
# --------------------------------------------------------------------------

def _extract_json(text: str) -> Optional[dict]:
    """Pull the first JSON object out of a model response. Tolerates code
    fences and leading/trailing prose."""
    if not text:
        return None
    # Strip ```json ... ``` fences if present.
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        # Fall back to the first balanced-looking {...} span.
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidate = text[start:end + 1]
    if not candidate:
        return None
    try:
        obj = json.loads(candidate)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def _clamp_score(value) -> Optional[int]:
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return max(0, min(100, n))


# --------------------------------------------------------------------------
# Prompts
# --------------------------------------------------------------------------

_SCORE_SYSTEM = """\
You are a precise job-fit scorer for a candidate. You are given the
candidate's CV and a single listing (a job or freelance gig). Score how well
the listing fits the candidate's background and their five target role
categories:
  1. Product design
  2. UI/UX design
  3. Agentic / AI software product work (AI-first product design, prototyping,
     "design engineer" roles with an AI bent)
  4. Pitch-deck / presentation design
  5. Quality assurance (QA)

Scoring guidance (0-100):
  85-100 strong, direct fit in a target category with matching skills
  65-84  clearly relevant; worth applying
  40-64  tangential or partial fit
  0-39   not a fit (wrong discipline, wrong seniority, requires skills the
         candidate lacks such as deep backend/ML engineering)

Remote or global roles are preferred (candidate is in Lagos, Nigeria).

SMALL-TEAM FIT (the candidate wants early, high-ownership roles):
  - BOOST strongly: "founding designer", "first design hire", "first
    designer", "design generalist", "0->1" / "0 to 1", "wear many hats";
    seed / pre-seed / Series A; and companies with a small open-role count
    (a "Company:" line below may say small/mid/large + how many open roles).
  - DOWN-RANK: very large / established companies (large open-role count);
    narrow senior-specialist roles that need deep enterprise experience.
  - When a role is a strong small-team / founding fit, SAY SO briefly in the
    reason (e.g. "founding designer at a seed fintech").

SENIORITY (a stated seniority preference is given with each listing):
  - PREFER mid-level / intermediate individual-contributor design roles
    (e.g. "Product Designer", "UX Designer", "Designer", "Designer II") --
    score these highest.
  - Meaningfully DOWN-RANK clearly-senior and management roles so they fall
    toward or below the keep line: Senior / Staff / Principal / Lead, and
    Head of / Director / Manager / VP, and roles demanding 8+ years or deep
    narrow specialization.
  - EXCEPTION: keep founding / first-designer / 0->1 roles high even if
    senior-titled -- those are the high-ownership early roles the candidate
    wants.

Reply with STRICT JSON only, no prose, no code fences:
{"score": <integer 0-100>, "reason": "<one short line, <= 18 words>"}"""

_OPENER_SYSTEM = """\
You draft a ONE-LINE outreach opener for the candidate to send to the maker of
a newly launched product, as a design services pitch.

The candidate's voice: warm, direct, confident, concise, human-sounding.
They prefer fixed project pricing, they mirror the client's own language, and
they end with a conversational question. The opener must:
  - sound like a real person who actually looked at the product,
  - reference what the product actually does (use its tagline/description),
  - hint at a concrete design angle (UI/UX polish, onboarding, pitch deck,
    motion, AI-product design) relevant to that product,
  - end with a conversational question,
  - be one or two sentences, never templated or salesy.

Also rate fit (0-100) for whether this product is a good design-services lead.
Reply with STRICT JSON only, no prose, no code fences:
{"fit": <integer 0-100>, "opener": "<one or two sentences>"}"""


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def score_listing(listing: Listing, cv: str) -> Optional[tuple[int, str]]:
    """Return (score, reason) or None if scoring failed (logged)."""
    size_line = ""
    if listing.company_size:
        n = listing.open_roles
        size_line = (f"Company: {listing.company} — {listing.company_size} team"
                     + (f" (~{n} open roles total)" if n is not None else "")
                     + "\n")
    user = (
        f"=== CANDIDATE CV ===\n{cv}\n\n"
        f"Seniority preference: {config.SENIORITY_PREFERENCE}\n\n"
        f"=== LISTING ({listing.stream}) ===\n{size_line}"
        f"{listing.text_for_scoring()}\n\n"
        "Score this listing now."
    )
    try:
        raw = _complete(_SCORE_SYSTEM, user, max_tokens=300)
    except Exception as e:
        global LAST_ERROR
        LAST_ERROR = str(e)
        log.warning("score failed for %s: %s", listing.job_id, e)
        return None
    obj = _extract_json(raw)
    if not obj:
        log.warning("score: unparseable JSON for %s: %r",
                    listing.job_id, raw[:120])
        return None
    score = _clamp_score(obj.get("score"))
    if score is None:
        log.warning("score: missing/invalid score for %s", listing.job_id)
        return None
    reason = str(obj.get("reason", "")).strip()[:200]
    return score, reason


def draft_lead(listing: Listing, cv: str) -> Optional[tuple[int, str]]:
    """Return (fit, opener) for a lead, or None if it failed (logged)."""
    tagline = listing.tags[0] if listing.tags else ""
    user = (
        f"=== CANDIDATE (for voice/skills) ===\n{cv}\n\n"
        f"=== PRODUCT ===\n"
        f"Name: {listing.title}\n"
        f"Tagline: {tagline}\n"
        f"Description: {listing.description}\n"
        f"URL: {listing.url}\n\n"
        "Draft the opener now."
    )
    try:
        raw = _complete(_OPENER_SYSTEM, user, max_tokens=300)
    except Exception as e:
        global LAST_ERROR
        LAST_ERROR = str(e)
        log.warning("opener failed for %s: %s", listing.job_id, e)
        return None
    obj = _extract_json(raw)
    if not obj:
        log.warning("opener: unparseable JSON for %s: %r",
                    listing.job_id, raw[:120])
        return None
    fit = _clamp_score(obj.get("fit"))
    opener = str(obj.get("opener", "")).strip()
    if fit is None or not opener:
        log.warning("opener: missing fit/opener for %s", listing.job_id)
        return None
    return fit, opener


_PITCH_SYSTEM = """\
You draft a ONE-LINE cold outreach opener for the candidate to send to a
company that is HIRING (growing) but currently has NO designer / no design
role posted -- a prime moment to pitch design services.

You are given the company name and a sample of its open (non-design) roles,
which hint at what the company does and its stage. Infer the domain (fintech,
dev tools, AI, health, e-commerce, etc.) from those roles.

The candidate's voice: warm, direct, confident, concise, human-sounding.
Fixed project pricing, mirrors the client's language, ends with a
conversational question. The opener must:
  - sound like a real person who looked at the company,
  - reference what the company likely does (from the roles/domain),
  - note (implicitly) they're scaling without dedicated design, and name a
    concrete design angle (onboarding, core flows, design system, visual
    polish, pitch deck),
  - end with a conversational question,
  - be one or two sentences, never templated or salesy.

Reply with STRICT JSON only, no prose, no code fences:
{"opener": "<one or two sentences>"}"""


def draft_pitch(company: str, role_titles: list[str],
                cv: str) -> Optional[str]:
    """One-line cold-pitch opener for a company hiring without a designer.
    Returns the opener, or None on failure (logged)."""
    roles = ", ".join(role_titles[:6]) if role_titles else \
        "(no role titles available)"
    user = (
        f"=== CANDIDATE (for voice/skills) ===\n{cv}\n\n"
        f"=== COMPANY ===\nName: {company}\n"
        f"Currently open roles (non-design): {roles}\n\n"
        "Draft the opener now."
    )
    try:
        raw = _complete(_PITCH_SYSTEM, user, max_tokens=300)
    except Exception as e:
        global LAST_ERROR
        LAST_ERROR = str(e)
        log.warning("pitch failed for %s: %s", company, e)
        return None
    obj = _extract_json(raw)
    if not obj:
        log.warning("pitch: unparseable JSON for %s: %r", company, raw[:120])
        return None
    opener = str(obj.get("opener", "")).strip()
    return opener or None
