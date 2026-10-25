"""Shared helpers: logging, HTTP, keyword matching, dates.

Deliberately tiny and dependency-light so every source module can lean on
it without pulling in surprises.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from . import config

log = logging.getLogger("job-agent")


def setup_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

_DEFAULT_HEADERS = {"User-Agent": config.USER_AGENT, "Accept": "application/json"}


def _client(headers: Optional[dict] = None, timeout: float = 25.0) -> httpx.Client:
    merged = dict(_DEFAULT_HEADERS)
    if headers:
        merged.update(headers)
    # trust_env=True lets httpx honor HTTPS_PROXY / CA bundle from the env.
    return httpx.Client(headers=merged, timeout=timeout, follow_redirects=True,
                        trust_env=True)


def get_json(url: str, *, params: Optional[dict] = None,
             headers: Optional[dict] = None, timeout: float = 25.0) -> Any:
    """GET and parse JSON. Raises on HTTP error -- callers wrap this."""
    with _client(headers, timeout) as c:
        resp = c.get(url, params=params)
        resp.raise_for_status()
        return resp.json()


def get_text(url: str, *, headers: Optional[dict] = None,
             timeout: float = 25.0) -> str:
    with _client(headers, timeout) as c:
        resp = c.get(url)
        resp.raise_for_status()
        return resp.text


def post_json(url: str, *, json_body: dict, headers: Optional[dict] = None,
              timeout: float = 30.0) -> Any:
    with _client(headers, timeout) as c:
        resp = c.post(url, json=json_body)
        resp.raise_for_status()
        return resp.json()


def polite_sleep() -> None:
    """Small delay between external calls to be a good API citizen."""
    time.sleep(config.REQUEST_DELAY_SECONDS)


# --------------------------------------------------------------------------
# Text / keyword helpers
# --------------------------------------------------------------------------

def matches_any(text: str, keywords: list[str]) -> bool:
    low = (text or "").lower()
    return any(kw in low for kw in keywords)


def clip(text: str, limit: int = config.DESCRIPTION_MAX_CHARS) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + " …"


def strip_html(text: str) -> str:
    """Very light HTML -> text. Good enough for scoring context."""
    import re
    if not text:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    # unescape a few common entities
    for a, b in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                 ("&#39;", "'"), ("&quot;", '"'), ("&nbsp;", " ")):
        text = text.replace(a, b)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------

def now_local_str() -> str:
    """Today's date in the configured timezone, as YYYY-MM-DD."""
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(config.TIMEZONE)
    except Exception:  # pragma: no cover - zoneinfo should exist on 3.11+
        tz = timezone.utc
    return datetime.now(tz).strftime("%Y-%m-%d")


def to_iso(value: Any) -> str:
    """Best-effort normalization of assorted date shapes to an ISO string."""
    if value in (None, ""):
        return ""
    # already a string: trust it
    if isinstance(value, str):
        return value
    # unix timestamp (int/float)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return str(value)
    return str(value)


def parse_dt(value: Any) -> "datetime | None":
    """Best-effort parse of assorted date values to an aware UTC datetime.

    Handles ISO-8601 (incl. trailing Z and offsets), plain dates, RFC-822
    (RSS feeds), and unix timestamps. Returns None if unparseable -- callers
    decide whether an undatable listing is kept or dropped.
    """
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    s = str(value).strip()
    if not s:
        return None
    # ISO-8601 (fromisoformat on 3.11+ accepts most forms; normalize Z).
    iso = s[:-1] + "+00:00" if s.endswith("Z") else s
    try:
        dt = datetime.fromisoformat(iso)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    # RFC-822 (e.g. "Mon, 20 Oct 2026 12:00:00 +0000") from RSS feeds.
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(s)
        if dt is not None:
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, IndexError):
        pass
    return None


# --------------------------------------------------------------------------
# Company size + slug helpers (for discovery / small-team tagging)
# --------------------------------------------------------------------------

def company_size(n: "int | None") -> str:
    """Bucket a board's open-role count into small/mid/large (thresholds in
    config). Empty string when the count is unknown."""
    if n is None:
        return ""
    if n <= config.COMPANY_SIZE_SMALL_MAX:
        return "small"
    if n <= config.COMPANY_SIZE_MID_MAX:
        return "mid"
    return "large"


def slug_variants(name: str) -> list[str]:
    """Candidate URL slugs for a company name, most-likely first.

    "Radical AI" -> ["radicalai", "radical-ai"]; "decagon" -> ["decagon"].
    Only space-free forms are returned (slugs can't contain spaces).
    """
    import re
    s = (name or "").strip().lower()
    if not s:
        return []
    collapsed = re.sub(r"[^a-z0-9]+", "", s)              # radicalai
    dotted = re.sub(r"[^a-z0-9.]+", "", s).strip(".")     # cal.com (domain-style)
    hyphenated = re.sub(r"[^a-z0-9]+", "-", s).strip("-")  # radical-ai
    out: list[str] = []
    for v in (collapsed, dotted, hyphenated):
        if v and v not in out:
            out.append(v)
    return out
