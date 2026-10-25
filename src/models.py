"""Normalized data model shared across every source.

Every source adapter returns a list of `Listing`. Nothing downstream
(scoring, dedupe, sheet-writing) knows or cares which API a listing came
from -- that is the whole point of normalizing here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# Valid values for Listing.stream -> which sheet tab a listing lands in.
STREAM_JOB = "job"
STREAM_GIG = "gig"
STREAM_LEAD = "lead"


@dataclass
class Listing:
    """One normalized opportunity from any source.

    Fields above the divider are populated at fetch time. Fields below it
    are populated later: `score`/`reason` after LLM scoring (jobs + gigs),
    and `maker`/`contact`/`website`/`opener` for leads.
    """

    source: str                       # e.g. "remoteok", "reddit:forhire"
    source_id: str                    # stable id from the source
    stream: str                       # STREAM_JOB | STREAM_GIG | STREAM_LEAD

    title: str = ""
    company: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    salary: str = ""                  # free-text; sources are inconsistent
    url: str = ""
    posted_at: str = ""               # ISO-8601 string if known, else ""

    # ---- company-size signal (set by ATS adapters) ----
    company_size: str = ""            # "small" | "mid" | "large" | ""
    open_roles: Optional[int] = None  # total open roles on the company's board

    # ---- populated after scoring ----
    score: Optional[int] = None       # 0-100
    reason: str = ""                  # one short line

    # ---- leads only ----
    maker: str = ""
    contact: str = ""                 # e.g. "@handle" / email
    website: str = ""
    opener: str = ""                  # AI-drafted outreach opener

    @property
    def job_id(self) -> str:
        """Stable dedupe key, also written into the sheet.

        `source` already encodes the sub-source (e.g. "reddit:forhire"),
        so `{source}:{source_id}` is globally unique and reproducible.
        """
        return f"{self.source}:{self.source_id}"

    def text_for_scoring(self) -> str:
        """Compact blob handed to the LLM for scoring."""
        parts = [
            f"Title: {self.title}",
            f"Company: {self.company}" if self.company else "",
            f"Tags: {', '.join(self.tags)}" if self.tags else "",
            f"Salary: {self.salary}" if self.salary else "",
            "",
            (self.description or "").strip(),
        ]
        return "\n".join(p for p in parts if p != "")
