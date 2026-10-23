# Automated Job Search — Daily Job & Lead Agent

A Python agent that runs **once a day** and:

1. **Pulls** job listings, freelance gigs, and new-startup leads from free,
   ToS-compliant public APIs.
2. **Scores** each listing against your CV with an LLM (0–100 + a one-line
   reason).
3. **Dedupes** against what it already found (the Google Sheet is the source
   of truth — no state file).
4. **Writes** the good ones (score ≥ threshold, highest first) into a Google
   Sheet with three tabs.
5. Runs on a schedule via **GitHub Actions** (plus a manual-run button).

Built for **David Ayewah** — Product Designer & UI/UX Designer (~5 yrs,
Lagos, remote/global), leaning into AI/agentic product work and pitch-deck
design. Scoring targets five role categories: product design, UI/UX,
agentic/AI product work, pitch-deck/presentation design, and QA.

---

## How it works

```
Pull ──▶ Score ──▶ Dedupe ──▶ Write ──▶ (GitHub Actions cron)
sources   LLM      vs sheet    3 tabs
```

Every source is normalized into one `Listing` dataclass before scoring, so
the pipeline is source-agnostic. Each source lives in its own module under
`src/sources/` and exposes `fetch() -> list[Listing]`. **If one source fails
(network, API shape change, empty), it logs a warning and is skipped — the
run never crashes, and every other source still writes.**

### Three output streams (tabs)

| Tab       | What lands there |
|-----------|------------------|
| **Jobs**  | Scored listings from the job-board APIs. |
| **Gigs**  | Freelance + pitch-deck leads from Reddit, plus any job-board role matching the pitch-deck/freelance keywords. |
| **Leads** | New startups to pitch, from Product Hunt (+ optional Show HN). Each lead also gets an AI-drafted one-line outreach opener in your voice. |

### Sources (all free, keyless unless noted)

- **Jobs:** Remotive, RemoteOK, Arbeitnow, Jobicy, Himalayas, Working Nomads,
  We Work Remotely (RSS), Hacker News "Who is hiring" (Algolia API).
- **Jobs — direct from company career pages:** Greenhouse, Lever, Ashby, and
  SmartRecruiters public JSON APIs for a curated list of companies you track
  (`TARGET_COMPANIES` in `src/config.py`). This is the same data that powers
  each company's own Careers page — not web-scraping — so it's keyless,
  stable, and ToS-friendly. Roles are keyword-filtered to your target
  categories and land in the Jobs tab. See "Tracking companies directly" below.
- **Gigs:** Reddit (`forhire`, `jobbit`, `DesignJobs`, `UI_Design`,
  `graphic_design`, `startups`, `hiring`) via the keyless `.json` path.
- **Leads:** Product Hunt (GraphQL, needs a free token), Show HN (optional,
  off by default).

> **Honest gap:** most pitch-deck gigs live on Upwork / Contra / Fiverr,
> which have **no free API** and forbid scraping. The Gigs tab is therefore
> Reddit + keyword matches on the job feeds — not full pitch-deck coverage.
> Wellfound/AngelList and arc.dev are intentionally **not** used (no free
> API, anti-bot, ToS forbids automation) — keep those as manual channels.

---

## The Google Sheet

One spreadsheet, three tabs. Tabs and header rows are created automatically
on the first run if missing. New rows are appended at the bottom; the agent
**never** touches the `Applied?` / `Status` / `Reached Out?` columns — those
are yours to fill in. Dates are written in **Africa/Lagos** time (UTC+1).

- **Jobs:** `Date Found · Score · Role · Company · Why · Salary · Source · Link · JobID · Applied?`
- **Gigs:** `Date Found · Score · Title · Source/Sub · Why · Budget/Notes · Source · Link · JobID · Status`
- **Leads:** `Date Found · Product · Tagline · Fit · Opener (draft) · Maker · X / Contact · PH Link · Website? · PostID · Reached Out?`

`JobID`/`PostID` = `{source}:{source_id}`, the dedupe key. Running the agent
twice back-to-back adds **zero** duplicate rows.

---

## Setup

You need: this repo, a Google service account + shared sheet, an LLM API key,
and a free Product Hunt token.

### 1. Your CV

Paste your full CV into **`data/cv.md`** (replace the placeholder). The
scoring logic reads this whole file as context, so you can edit it any time
without touching code. If it's missing/empty, the agent falls back to a short
built-in profile summary and logs a warning.

### 2. Google service account + sheet

1. Go to the [Google Cloud Console](https://console.cloud.google.com/) and
   create (or pick) a project.
2. **Enable the APIs:** APIs & Services → Library → enable **Google Sheets
   API** *and* **Google Drive API**.
3. **Create a service account:** IAM & Admin → Service Accounts → *Create
   service account*. Give it a name; no roles needed.
4. **Make a key:** open the service account → *Keys* → *Add key* → *Create new
   key* → **JSON**. A `.json` file downloads. Keep it secret.
5. **Create your Google Sheet.** Copy its **spreadsheet id** from the URL:
   `https://docs.google.com/spreadsheets/d/`**`THIS_LONG_ID`**`/edit`.
6. **Share the sheet** with the service account's `client_email` (found in the
   JSON, looks like `...@...iam.gserviceaccount.com`) as **Editor**. This step
   is what lets the agent write — skip it and every write fails with a
   permission error.

### 3. LLM key

- **Anthropic (default):** get an `ANTHROPIC_API_KEY`. Scoring uses a current
  fast, low-cost model (`claude-haiku-5-5` by default). The provider and model
  are behind env vars (`LLM_PROVIDER`, `LLM_MODEL`) so they're swappable —
  set `LLM_PROVIDER=openai` + `OPENAI_API_KEY` to use OpenAI instead.
- Pick a **current** low-cost model string for `LLM_MODEL`; don't pin a stale
  one.

### 4. Product Hunt token (for Leads)

Create a free developer token at
<https://www.producthunt.com/v2/oauth/applications> → *Create an application*
→ use the **Developer Token**. Without it, the Leads tab is simply skipped
(with a warning) — the rest of the run is unaffected.

---

## Running locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env      # then fill in your real values
# (.env is git-ignored)

# Fetch + score + PRINT, without touching the sheet:
python -m src.main --dry-run

# Full run (writes to the sheet):
python -m src.main

# Only specific sources:
python -m src.main --only remoteok reddit_gigs --dry-run
```

`--dry-run` tolerates a missing LLM key (it will fetch and print only).
A full run requires an LLM key (it can't score without one).

Each run ends with a per-source summary: `fetched / new / written`.

---

## Running on GitHub Actions

The workflow `.github/workflows/daily.yml` runs daily at **05:00 UTC
(06:00 Africa/Lagos)** and can also be triggered manually (Actions tab →
*Daily Job & Lead Agent* → *Run workflow*).

### Set the repository secrets

Repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Required? | Value |
|--------|-----------|-------|
| `GOOGLE_SERVICE_ACCOUNT_JSON` | ✅ | The **entire** service-account JSON, pasted as one blob. |
| `GOOGLE_SHEET_ID` | ✅ | The spreadsheet id from the sheet URL. |
| `ANTHROPIC_API_KEY` | ✅ (or OpenAI) | Your Anthropic key. |
| `PRODUCTHUNT_TOKEN` | ⬜ | Free PH developer token (Leads tab). |
| `LLM_MODEL` | ⬜ | Override the default model string. |
| `LLM_PROVIDER` | ⬜ | `anthropic` (default) or `openai`. |
| `OPENAI_API_KEY` | ⬜ | Only if `LLM_PROVIDER=openai`. |
| `SCORE_THRESHOLD` | ⬜ | Override the default `65`. |

No secrets are ever stored in code or committed files — they come only from
the environment / GitHub secrets.

### Changing the schedule or threshold

- **Schedule:** edit the `cron` line in `.github/workflows/daily.yml`. It's in
  UTC; the file has a comment with the Lagos offset.
- **Threshold:** set the `SCORE_THRESHOLD` secret, or edit the default in
  `src/config.py`.

---

## Tuning (`src/config.py`)

Everything a non-coder would reasonably want to change lives here with
comments: source on/off toggles, the score threshold, per-source fetch caps,
the subreddit list, the Product Hunt topic list, the pitch-deck keyword list,
the category keyword list, and the timezone.

**Freshness:** the APIs serve only currently-open roles, and on top of that
`MAX_POSTING_AGE_DAYS` (default **7**) keeps only listings posted within that
window; set it to `0` to disable. Listings whose source gives no usable date
are kept by default (`DROP_UNDATED_LISTINGS = False`). Each tab is still
sorted best-fit (score) first — now over a fresh, open pool.

### Tracking companies directly

Edit `TARGET_COMPANIES` in `src/config.py` to follow specific companies'
career pages via their ATS. Each entry is the platform + the company's
**slug**, which you read straight from its careers URL:

| ATS | Careers URL looks like | Entry |
|-----|------------------------|-------|
| Greenhouse | `boards.greenhouse.io/`**`stripe`** | `{"ats": "greenhouse", "slug": "stripe", "name": "Stripe"}` |
| Lever | `jobs.lever.co/`**`mozilla`** | `{"ats": "lever", "slug": "mozilla", "name": "Mozilla"}` |
| Ashby | `jobs.ashbyhq.com/`**`linear`** (or **`linear`**`.ashbyhq.com`) | `{"ats": "ashby", "slug": "linear", "name": "Linear"}` |
| SmartRecruiters | `jobs.smartrecruiters.com/`**`Square`** | `{"ats": "smartrecruiters", "slug": "Square", "name": "Square"}` |

The repo ships a **starter list** — best-effort examples across fintech /
SaaS / dev-tools. A company can change ATS or slug; if so, that entry simply
returns nothing and the run logs a warning. The per-company counts in the run
logs (`greenhouse[stripe]: N matched`) show which entries are producing
results, so you can prune and replace them with the companies you actually
want. Not every employer uses one of these three systems, but a large share
of startups and tech companies do.

---

## Project layout

```
.github/workflows/daily.yml   # cron + manual trigger
src/
  main.py                     # pipeline: pull → score → dedupe → write
  config.py                   # all the knobs
  models.py                   # Listing dataclass (normalized schema)
  scoring.py                  # LLM scoring + lead-opener drafting (prompts here)
  sheets.py                   # gspread read/write + dedupe
  util.py                     # http, logging, keyword/date helpers
  sources/                    # one module per source, each fetch()->list[Listing]
data/cv.md                    # paste your CV here
requirements.txt
.env.example
```

## Notes on good-API-citizen behavior

- Remotive is call-capped (≤ 4/run) and credited (its posting URL is the link).
- RemoteOK gets a descriptive User-Agent and the first (legal) array element
  is skipped.
- Reddit uses the keyless `.json` path with a descriptive User-Agent; a clean
  upgrade path to PRAW + OAuth is reserved (`REDDIT_*` env vars) for later.
- Small delays are inserted between external calls.
