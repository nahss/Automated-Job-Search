"""Google Sheets I/O: connect, ensure tabs/headers, dedupe, append.

The sheet is the single source of truth for dedupe -- there is no separate
state file. Before writing a tab we read its existing id column and skip
anything already present, so re-running the agent adds zero duplicates.

The user-owned status columns (Applied? / Status / Reached Out?) are never
written by the agent: new rows leave those cells blank for the human.
"""

from __future__ import annotations

import json
import os
from typing import Iterable, Optional

import gspread
from google.oauth2.service_account import Credentials

from . import config
from .models import Listing, STREAM_JOB, STREAM_GIG, STREAM_LEAD
from .util import log, now_local_str

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Tab names and their header rows. The id column (JobID / PostID) is the
# dedupe key; we look it up by header name so column order can change safely.
TABS: dict[str, dict] = {
    STREAM_JOB: {
        "title": "Jobs",
        "headers": ["Date Found", "Score", "Role", "Company", "Why",
                    "Salary", "Source", "Link", "JobID", "Applied?"],
        "id_header": "JobID",
    },
    STREAM_GIG: {
        "title": "Gigs",
        "headers": ["Date Found", "Score", "Title", "Source/Sub", "Why",
                    "Budget/Notes", "Source", "Link", "JobID", "Status"],
        "id_header": "JobID",
    },
    STREAM_LEAD: {
        "title": "Leads",
        "headers": ["Date Found", "Product", "Tagline", "Fit",
                    "Opener (draft)", "Maker", "X / Contact", "PH Link",
                    "Website?", "PostID", "Reached Out?"],
        "id_header": "PostID",
    },
}


class SheetClient:
    """Thin wrapper around one spreadsheet with our three tabs."""

    def __init__(self, spreadsheet) -> None:
        self._ss = spreadsheet
        self._ws_cache: dict[str, gspread.Worksheet] = {}

    # ---- connection ----
    @classmethod
    def connect(cls) -> "SheetClient":
        sheet_id = os.environ.get("GOOGLE_SHEET_ID")
        if not sheet_id:
            raise RuntimeError("GOOGLE_SHEET_ID is not set")
        creds = _load_credentials()
        gc = gspread.authorize(creds)
        ss = gc.open_by_key(sheet_id)
        log.info("Connected to spreadsheet: %s", ss.title)
        return cls(ss)

    # ---- tabs ----
    def worksheet(self, stream: str) -> gspread.Worksheet:
        """Return the worksheet for a stream, creating it (with headers) if
        it does not yet exist."""
        if stream in self._ws_cache:
            return self._ws_cache[stream]
        spec = TABS[stream]
        title = spec["title"]
        try:
            ws = self._ss.worksheet(title)
        except gspread.WorksheetNotFound:
            log.info("Creating missing tab: %s", title)
            ws = self._ss.add_worksheet(title=title, rows=1000,
                                        cols=max(12, len(spec["headers"])))
            ws.append_row(spec["headers"], value_input_option="RAW")
        else:
            # Ensure the header row is present (new/blank tab).
            first = ws.row_values(1)
            if not first:
                ws.update("A1", [spec["headers"]], value_input_option="RAW")
        self._ws_cache[stream] = ws
        return ws

    # ---- dedupe ----
    def existing_ids(self, stream: str) -> set[str]:
        """The set of ids already present in the tab's id column."""
        spec = TABS[stream]
        ws = self.worksheet(stream)
        header = ws.row_values(1)
        try:
            col_idx = header.index(spec["id_header"]) + 1  # gspread is 1-based
        except ValueError:
            log.warning("%s: id column %r not found in header; dedupe disabled",
                        spec["title"], spec["id_header"])
            return set()
        values = ws.col_values(col_idx)[1:]  # drop header cell
        return {v.strip() for v in values if v.strip()}

    # ---- writing ----
    def append_rows(self, stream: str, rows: list[list]) -> int:
        if not rows:
            return 0
        ws = self.worksheet(stream)
        ws.append_rows(rows, value_input_option="USER_ENTERED")
        return len(rows)


# --------------------------------------------------------------------------
# Row builders: Listing -> row matching each tab's header order.
# --------------------------------------------------------------------------

def _job_row(l: Listing, date_found: str) -> list:
    return [date_found, l.score, l.title, l.company, l.reason,
            l.salary, l.source, l.url, l.job_id, ""]  # Applied? left blank


def _gig_row(l: Listing, date_found: str) -> list:
    return [date_found, l.score, l.title, l.source, l.reason,
            l.salary, l.source, l.url, l.job_id, ""]  # Status left blank


def build_row(l: Listing, date_found: str) -> list:
    if l.stream == STREAM_JOB:
        return _job_row(l, date_found)
    if l.stream == STREAM_GIG:
        return _gig_row(l, date_found)
    if l.stream == STREAM_LEAD:
        # Leads: Date | Product | Tagline | Fit | Opener | Maker | Contact |
        #        PH Link | Website? | PostID | Reached Out?
        website = l.website if l.website else "No"
        tagline = l.tags[0] if l.tags else ""
        return [date_found, l.title, tagline, l.score, l.opener,
                l.maker, l.contact, l.url, website, l.job_id, ""]
    raise ValueError(f"unknown stream: {l.stream}")


def write_listings(client: SheetClient, stream: str,
                   listings: list[Listing]) -> int:
    """Append already-scored, already-deduped listings to a tab."""
    date_found = now_local_str()
    rows = [build_row(l, date_found) for l in listings]
    return client.append_rows(stream, rows)


# --------------------------------------------------------------------------
# Credentials
# --------------------------------------------------------------------------

def _load_credentials() -> Credentials:
    """Build service-account credentials from the env.

    Accepts either the full JSON blob in GOOGLE_SERVICE_ACCOUNT_JSON, or a
    path to a JSON file in GOOGLE_SERVICE_ACCOUNT_FILE.
    """
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if raw:
        try:
            info = json.loads(raw)
        except json.JSONDecodeError as e:
            raise RuntimeError(
                "GOOGLE_SERVICE_ACCOUNT_JSON is set but is not valid JSON"
            ) from e
        return Credentials.from_service_account_info(info, scopes=_SCOPES)

    path = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    if path and os.path.exists(path):
        return Credentials.from_service_account_file(path, scopes=_SCOPES)

    raise RuntimeError(
        "No Google credentials found. Set GOOGLE_SERVICE_ACCOUNT_JSON "
        "(full JSON, one line) or GOOGLE_SERVICE_ACCOUNT_FILE (path)."
    )
