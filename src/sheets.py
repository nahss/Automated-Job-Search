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
from .models import (Listing, STREAM_JOB, STREAM_GIG, STREAM_LEAD,
                     STREAM_PITCH, STREAM_AFRICA)
from .util import log, now_local_str, content_key

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
                    "Salary", "Source", "Link", "JobID", "Applied?",
                    "Company size"],
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
    STREAM_PITCH: {
        "title": "Pitch",
        "headers": ["Date Found", "Company", "ATS", "Open roles", "Size",
                    "Opener (draft)", "Board Link", "CompanyID",
                    "Reached Out?"],
        "id_header": "CompanyID",
    },
    STREAM_AFRICA: {
        "title": "Africa",
        "headers": ["Date Found", "Score", "Role", "Company", "Location",
                    "Why", "Salary", "Source", "Link", "JobID", "Applied?",
                    "Company size"],
        "id_header": "JobID",
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
            # Ensure the header row is present and up to date.
            first = ws.row_values(1)
            headers = spec["headers"]
            if not first:
                ws.update("A1", [headers], value_input_option="RAW")
            elif first != headers and headers[:len(first)] == first:
                # We only ever append columns (e.g. "Company size"); extend the
                # existing header without disturbing the data already in place.
                log.info("%s: extending header %d -> %d cols",
                         title, len(first), len(headers))
                ws.update("A1", [headers], value_input_option="RAW")
        self._ws_cache[stream] = ws
        return ws

    # ---- discovery watchlist (resolver cache + discovered boards) ----
    def _watchlist_ws(self) -> gspread.Worksheet:
        title = config.WATCHLIST_TAB
        try:
            return self._ss.worksheet(title)
        except gspread.WorksheetNotFound:
            log.info("Creating watchlist tab: %s", title)
            ws = self._ss.add_worksheet(title=title, rows=2000, cols=5)
            ws.append_row(["Candidate", "ATS", "Board Slug", "Source",
                           "First Seen"], value_input_option="RAW")
            return ws

    def read_watchlist(self) -> tuple[set[str], list[dict]]:
        """Return (candidates already attempted, resolved boards)."""
        ws = self._watchlist_ws()
        rows = ws.get_all_values()[1:]  # drop header
        attempted: set[str] = set()
        discovered: list[dict] = []
        for r in rows:
            cand = (r[0] if len(r) > 0 else "").strip()
            ats = (r[1] if len(r) > 1 else "").strip()
            slug = (r[2] if len(r) > 2 else "").strip()
            src = (r[3] if len(r) > 3 else "").strip()
            if cand:
                attempted.add(cand)
            if ats and slug:
                discovered.append({"ats": ats, "slug": slug, "name": slug,
                                   "source": src or "discovered"})
        return attempted, discovered

    def append_watchlist(self, rows: list[list]) -> int:
        if not rows:
            return 0
        self._watchlist_ws().append_rows(rows, value_input_option="RAW")
        return len(rows)

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

    def existing_content_keys(self, stream: str) -> set[str]:
        """Normalized (company|role) keys already in a tab, so the SAME role
        from a different source isn't written twice. Only for tabs that have
        both Role and Company columns (Jobs, Africa)."""
        spec = TABS[stream]
        headers = spec["headers"]
        if "Role" not in headers or "Company" not in headers:
            return set()
        ws = self.worksheet(stream)
        rows = ws.get_all_values()
        if len(rows) < 2:
            return set()
        hdr = rows[0]
        try:
            ri, ci = hdr.index("Role"), hdr.index("Company")
        except ValueError:
            return set()
        out: set[str] = set()
        for r in rows[1:]:
            role = r[ri] if len(r) > ri else ""
            comp = r[ci] if len(r) > ci else ""
            k = content_key(comp, role)
            if k:
                out.add(k)
        return out

    # ---- writing ----
    def append_rows(self, stream: str, rows: list[list]) -> int:
        if not rows:
            return 0
        ws = self.worksheet(stream)
        ws.append_rows(rows, value_input_option="USER_ENTERED")
        return len(rows)

    # ---- maintenance ----
    def purge_senior_rows(self, stream: str) -> tuple[int, int]:
        """Delete senior-titled rows from a tab. Rows whose status column
        (Applied?/Status/Reached Out?) is filled are KEPT, so tracked
        applications aren't lost. Returns (removed, kept_because_tracked)."""
        from .util import title_is_senior
        headers = TABS[stream]["headers"]
        role_col = ("Role" if "Role" in headers
                    else "Title" if "Title" in headers else None)
        if role_col is None:
            return (0, 0)
        status_col = next((h for h in ("Applied?", "Status", "Reached Out?")
                           if h in headers), None)
        ws = self.worksheet(stream)
        rows = ws.get_all_values()
        if len(rows) < 2:
            return (0, 0)
        hdr = rows[0]
        if role_col not in hdr:
            return (0, 0)
        ri = hdr.index(role_col)
        si = hdr.index(status_col) if (status_col and status_col in hdr) else None
        kept = [hdr]
        removed = kept_tracked = 0
        for r in rows[1:]:
            role = r[ri] if len(r) > ri else ""
            status = r[si].strip() if (si is not None and len(r) > si) else ""
            if title_is_senior(role):
                if status:
                    kept.append(r)
                    kept_tracked += 1
                else:
                    removed += 1
            else:
                kept.append(r)
        if removed:
            ws.clear()
            ws.update("A1", kept, value_input_option="USER_ENTERED")
        return (removed, kept_tracked)


# --------------------------------------------------------------------------
# Row builders: Listing -> row matching each tab's header order.
# --------------------------------------------------------------------------

def _size_cell(l: Listing) -> str:
    """e.g. 'small (11)' -- size bucket + total open roles, when known."""
    if l.company_size and l.open_roles is not None:
        return f"{l.company_size} ({l.open_roles})"
    return l.company_size or ""


def _job_row(l: Listing, date_found: str) -> list:
    return [date_found, l.score, l.title, l.company, l.reason,
            l.salary, l.source, l.url, l.job_id, "",  # Applied? left blank
            _size_cell(l)]


def _gig_row(l: Listing, date_found: str) -> list:
    return [date_found, l.score, l.title, l.source, l.reason,
            l.salary, l.source, l.url, l.job_id, ""]  # Status left blank


def _pitch_row(l: Listing, date_found: str) -> list:
    ats = l.tags[0] if l.tags else ""
    return [date_found, l.company, ats, l.open_roles, l.company_size,
            l.opener, l.url, l.job_id, ""]  # Reached Out? left blank


def _africa_row(l: Listing, date_found: str) -> list:
    location = l.location or (", ".join(l.tags) if l.tags else "")
    return [date_found, l.score, l.title, l.company, location, l.reason,
            l.salary, l.source, l.url, l.job_id, "",  # Applied? left blank
            _size_cell(l)]


def build_row(l: Listing, date_found: str) -> list:
    if l.stream == STREAM_JOB:
        return _job_row(l, date_found)
    if l.stream == STREAM_GIG:
        return _gig_row(l, date_found)
    if l.stream == STREAM_PITCH:
        return _pitch_row(l, date_found)
    if l.stream == STREAM_AFRICA:
        return _africa_row(l, date_found)
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
