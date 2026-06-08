"""
Operator Openings — Job Fetcher (Rolo)
Runs every Monday via GitHub Actions.

Blair's brief:
- Remote-first (remote + hybrid weighted higher)
- Senior / VP / Head / Director level only (big salary roles)
- CPG-led brands and DTC / Amazon brands specifically
- Cool titles: Head of eCommerce, Head of Amazon Marketplace,
  VP Supply Chain, Channel Manager, Head of Global Supply Chain
- No 3PLs, no logistics operators, no retailers
- Max 20 curated results per week in the sheet
"""

import os
import json
import re
import requests
import gspread
from datetime import datetime, timedelta
from google.oauth2.service_account import Credentials

# ── Config ────────────────────────────────────────────────────────────────────

RAPIDAPI_KEY    = os.environ["RAPIDAPI_KEY"]
SHEET_ID        = os.environ["GOOGLE_SHEET_ID"]
SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]

SLACK_CHANNEL   = "C0B0SNVK84C"   # #newsletter
HARRISON_ID     = "U0947B880H1"   # Harrison McIntyre-Miller

JSEARCH_URL     = "https://jsearch.p.rapidapi.com/search"
JSEARCH_HEADERS = {
    "X-RapidAPI-Key":  RAPIDAPI_KEY,
    "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
    "Content-Type":    "application/json",
}

MAX_JOBS_PER_WEEK = 20   # Blair's cap — quality over quantity

# ── Queries ───────────────────────────────────────────────────────────────────
# FIX: Simplified queries — brand/DTC/CPG qualifiers removed from query string.
# The title filter below does the curation. Over-qualified queries return 0 results.
# 13 queries × 4 weeks = 52 calls/month (within 200 free limit)

QUERIES = [
    # Head of eCommerce
    ("Head of eCommerce",             "United States"),
    ("Head of eCommerce",             "Canada"),

    # Amazon marketplace / channel roles
    ("Head of Amazon Marketplace",    "United States"),
    ("Amazon Channel Manager remote", "United States"),
    ("Amazon Channel Manager",        "Canada"),

    # VP Supply Chain
    ("VP Supply Chain remote",        "United States"),
    ("VP Supply Chain",               "Canada"),

    # Director eCommerce
    ("Director of eCommerce remote",  "United States"),
    ("Director eCommerce",            "Canada"),

    # Senior Director level
    ("Senior Director Amazon",        "United States"),
    ("Senior Director eCommerce",     "United States"),

    # Head of Supply Chain
    ("Head of Supply Chain",          "United States"),
    ("Head of Supply Chain",          "Canada"),
]

SHEET_HEADERS = [
    "Role Title",
    "Company",
    "Location",
    "Country",
    "Employment Type",
    "Apply Link",
    "Source",
    "Date Fetched",
    "Status",
]

# ── Title filter — Blair DOES want these ─────────────────────────────────────
# FIX: Expanded patterns using word-boundary regex so "director, dtc ecommerce"
# and "director/senior director" both match correctly regardless of punctuation.

TITLE_INCLUDE_PATTERNS = [
    r"head\s+of\s+e[\-\s]?commerce",
    r"head\s+of\s+amazon",
    r"head\s+of\s+marketplace",
    r"head\s+of\s+(global\s+)?supply\s+chain",
    r"head\s+of\s+supplier",
    r"vp\s+(of\s+)?e[\-\s]?commerce",
    r"vp\s+(of\s+)?supply\s+chain",
    r"vp\s+(of\s+)?amazon",
    r"vice\s+president.*e[\-\s]?commerce",
    r"vice\s+president.*supply\s+chain",
    r"director.*e[\-\s]?commerce",
    r"director.*amazon",
    r"director.*marketplace",
    r"director.*supply\s+chain",
    r"senior\s+director",
    r"senior\s+manager.*e[\-\s]?commerce",
    r"senior\s+manager.*amazon",
    r"channel\s+manager",
    r"amazon\s+channel",
    r"marketplace\s+manager",
    r"chief.*e[\-\s]?commerce",
    r"chief\s+commercial",
    r"general\s+manager.*e[\-\s]?commerce",
    r"global.*e[\-\s]?commerce",
]

# Pre-compile for speed
TITLE_INCLUDE_RE = [re.compile(p, re.IGNORECASE) for p in TITLE_INCLUDE_PATTERNS]

# ── Exclusion filters — Blair does NOT want these ─────────────────────────────
TITLE_EXCLUDE = [
    "3pl", "3p logistics", "warehouse", "fulfillment center",
    "logistics coordinator", "logistics manager", "freight",
    "dispatcher", "driver", "forklift", "picker", "packer",
    "retail store", "store manager", "branch manager",
    "recruiter", "talent acquisition", "hr manager",
    "software engineer", "data analyst", "accountant",
    "marketing manager",
]

COMPANY_EXCLUDE = [
    "virtualvocations", "solenis", "metro supply chain",
    "247 fulfillment", "north american freight",
    "shipbob", "flexport", "maersk", "fedex", "ups", "dhl",
    "xpo", "ryder", "ceva",
]

# Remote signals for location labelling + score boost
REMOTE_SIGNALS = [
    "remote", "work from home", "wfh", "hybrid", "anywhere",
    "distributed", "virtual",
]

# ── Scoring ───────────────────────────────────────────────────────────────────
# Higher score = surfaces first. Remote + senior + brand signals score highest.

SCORE_BOOST = {
    "head of":        10,
    "vp ":             9,
    "vice president":  9,
    "chief":           8,
    "senior director": 7,
    "director":        6,
    "amazon":          5,
    "dtc":             4,
    "cpg":             4,
    "fmcg":            4,
    "global":          3,
    "remote":          3,
    "hybrid":          2,
    "tiktok":          2,   # Blair liked Switch Energy — trend-forward brands
    "marketplace":     2,
}

def score_job(job: dict) -> int:
    combined = f"{job['role']} {job['company']} {job['location']}".lower()
    return sum(v for k, v in SCORE_BOOST.items() if k in combined)


# ── Filters ───────────────────────────────────────────────────────────────────

def passes_title_filter(title: str) -> bool:
    """Title must match at least one include pattern."""
    return any(r.search(title) for r in TITLE_INCLUDE_RE)

def fails_exclusion(title: str, company: str) -> bool:
    """Returns True if job should be rejected."""
    t = title.lower()
    c = company.lower()
    if any(ex in t for ex in TITLE_EXCLUDE):
        return True
    if any(ex in c for ex in COMPANY_EXCLUDE):
        return True
    return False

def is_remote_friendly(title: str, location: str) -> bool:
    combined = f"{title} {location}".lower()
    return any(sig in combined for sig in REMOTE_SIGNALS)


# ── Week label ────────────────────────────────────────────────────────────────

def get_week_label() -> str:
    """Always returns the Monday of the current week — e.g. 'Week of June 2, 2026'."""
    today  = datetime.today()
    monday = today - timedelta(days=today.weekday())
    return monday.strftime("Week of %B %-d, %Y")


# ── JSearch ───────────────────────────────────────────────────────────────────

def fetch_jobs(query: str, location: str, max_results: int = 10) -> list[dict]:
    """Call JSearch API and return normalised job dicts."""
    params = {
        "query":            f"{query} in {location}",
        "num_pages":        "1",
        "date_posted":      "week",
        "country":          "ca" if location == "Canada" else "us",
        "employment_types": "FULLTIME",
    }
    try:
        resp = requests.get(
            JSEARCH_URL, headers=JSEARCH_HEADERS, params=params, timeout=20
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
    except Exception as e:
        print(f"  ERROR fetching '{query}' / {location}: {e}")
        return []

    results = []
    for job in data[:max_results]:
        city      = job.get("job_city") or ""
        state     = job.get("job_state") or ""
        country   = job.get("job_country") or location
        loc_parts = [p for p in [city, state] if p]
        loc_str   = ", ".join(loc_parts) if loc_parts else location

        job_title = job.get("job_title", "").strip()
        is_remote = job.get("job_is_remote", False)

        # Label remote/hybrid clearly in location column
        if is_remote or is_remote_friendly(job_title, loc_str):
            if "remote" not in loc_str.lower() and "hybrid" not in loc_str.lower():
                loc_str = f"Remote ({loc_str})" if loc_str else "Remote"

        results.append({
            "role":     job_title,
            "company":  job.get("employer_name", "").strip(),
            "location": loc_str,
            "country":  country,
            "type":     (job.get("job_employment_type") or "Full-Time").replace("_", " ").title(),
            "link":     job.get("job_apply_link") or job.get("job_google_link", ""),
            "source":   "JSearch / Google for Jobs",
            "date":     datetime.today().strftime("%Y-%m-%d"),
            "status":   "Pending Review",
        })
    return results


# ── Google Sheets ─────────────────────────────────────────────────────────────

def get_workbook():
    """Authenticate via service account JSON from env var."""
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if not creds_json:
        raise EnvironmentError("GOOGLE_CREDENTIALS_JSON secret is not set.")
    creds_dict = json.loads(creds_json)
    scopes = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds  = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    return client.open_by_key(SHEET_ID)


def ensure_headers(worksheet):
    """Write, freeze, and format header row if not already present."""
    if worksheet.row_values(1) != SHEET_HEADERS:
        worksheet.insert_row(SHEET_HEADERS, index=1)
        worksheet.freeze(rows=1)
        worksheet.format("A1:I1", {
            "textFormat":      {"bold": True},
            "backgroundColor": {"red": 0.13, "green": 0.31, "blue": 0.55},
        })
        print("  Header row written and formatted.")


def get_or_create_tab(workbook, week_label: str):
    """Return worksheet for this week. New tabs are moved to position 0 (leftmost)."""
    try:
        ws = workbook.worksheet(week_label)
        print(f"  Found existing tab: '{week_label}'")
        return ws, False
    except gspread.WorksheetNotFound:
        ws = workbook.add_worksheet(
            title=week_label, rows=50, cols=len(SHEET_HEADERS)
        )
        workbook.reorder_worksheets(
            [ws] + [s for s in workbook.worksheets() if s.id != ws.id]
        )
        print(f"  Created new tab: '{week_label}' — moved to first position")
        return ws, True


# ── Slack ─────────────────────────────────────────────────────────────────────

def post_slack_notification(week_label: str, total_added: int, sheet_url: str):
    blocks = [
        {
            "type": "header",
            "text": {
                "type":  "plain_text",
                "text":  "📋  Open Job Opportunities for Inbound Newsletter — Sheet Updated",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"Hey <@{HARRISON_ID}> — this week's curated job listings are ready.\n\n"
                    f"*Week:* {week_label}\n"
                    f"*Listings added:* {total_added} curated roles\n"
                    f"*Filter:* Remote-first · Senior/VP/Head/Director · DTC & Amazon brands\n"
                    f"*Status:* All set to Pending Review"
                ),
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "*What to do:*\n"
                    "• Open the sheet — newest tab is first\n"
                    "• Pick 4–5 roles (mix CA + US, mix of cool titles)\n"
                    "• Paste into Beehiiv under Operator Openings\n"
                    "• Mark used roles as *Featured* in the Status column"
                ),
            },
            "accessory": {
                "type":  "button",
                "text":  {"type": "plain_text", "text": "Open Sheet", "emoji": True},
                "url":   sheet_url,
                "style": "primary",
            },
        },
        {"type": "divider"},
        {
            "type": "context",
            "elements": [{
                "type": "mrkdwn",
                "text": (
                    f"Rolo · Fetched via GitHub Actions · "
                    f"{datetime.today().strftime('%A %d %B %Y')}"
                ),
            }],
        },
    ]

    resp = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={
            "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
            "Content-Type":  "application/json",
        },
        json={
            "channel": SLACK_CHANNEL,
            "blocks":  blocks,
            "text": (
                f"Open Job Opportunities — {total_added} curated roles ready "
                f"for {week_label}"
            ),
        },
        timeout=10,
    )
    result = resp.json()
    if result.get("ok"):
        print("  Slack notification sent to #newsletter (tagged Harrison)")
    else:
        print(f"  Slack ERROR: {result.get('error', 'unknown error')}")


def post_slack_error(error_msg: str):
    requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={
            "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
            "Content-Type":  "application/json",
        },
        json={
            "channel": SLACK_CHANNEL,
            "text": (
                f"⚠️ <@{HARRISON_ID}> — Rolo job fetch *failed* this week.\n"
                f"Error: `{error_msg}`\n"
                f"Check GitHub Actions logs for details."
            ),
        },
        timeout=10,
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{'='*60}")
    print(f"  Rolo — Operator Openings Job Fetch")
    print(f"  {datetime.today().strftime('%A %d %B %Y')}")
    print(f"  Target: max {MAX_JOBS_PER_WEEK} curated roles")
    print(f"{'='*60}\n")

    week_label = get_week_label()
    sheet_url  = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}"

    try:
        print("Connecting to Google Sheets...")
        workbook       = get_workbook()
        ws, _          = get_or_create_tab(workbook, week_label)
        ensure_headers(ws)

        all_rows       = ws.get_all_values()
        existing_links = {r[5] for r in all_rows[1:] if len(r) > 5}

        # ── Fetch all candidates ──────────────────────────────────────────────
        all_candidates = []
        seen_links     = set()

        for query, location in QUERIES:
            print(f"\nFetching: '{query}' | {location}")
            jobs = fetch_jobs(query, location, max_results=10)
            print(f"  Raw results: {len(jobs)}")

            for job in jobs:
                # Skip already in sheet or already collected this run
                if job["link"] in existing_links or job["link"] in seen_links:
                    continue

                # Apply title filter
                if not passes_title_filter(job["role"]):
                    print(f"  SKIP (title filter): {job['role']}")
                    continue

                # Apply exclusion filter
                if fails_exclusion(job["role"], job["company"]):
                    print(f"  SKIP (exclusion): {job['role']} @ {job['company']}")
                    continue

                job["_score"] = score_job(job)
                all_candidates.append(job)
                seen_links.add(job["link"])
                print(f"  ✓ PASS (score {job['_score']}): {job['role']} @ {job['company']}")

        # ── Sort by score — best roles first ─────────────────────────────────
        all_candidates.sort(key=lambda j: j["_score"], reverse=True)
        final_jobs = all_candidates[:MAX_JOBS_PER_WEEK]

        print(f"\n{'─'*60}")
        print(f"  Candidates after filtering: {len(all_candidates)}")
        print(f"  Writing top {len(final_jobs)} to sheet")
        print(f"{'─'*60}")

        # ── Write to sheet ────────────────────────────────────────────────────
        total_added = 0
        for job in final_jobs:
            ws.append_row([
                job["role"],
                job["company"],
                job["location"],
                job["country"],
                job["type"],
                job["link"],
                job["source"],
                job["date"],
                job["status"],
            ])
            existing_links.add(job["link"])
            total_added += 1
            print(f"  + {job['role']} @ {job['company']} ({job['location']})")

        print(f"\n{'='*60}")
        print(f"  DONE — {total_added} roles written to '{week_label}'")
        print(f"  Sheet: {sheet_url}")
        print(f"{'='*60}\n")

        # ── Slack ─────────────────────────────────────────────────────────────
        if total_added > 0:
            print("Sending Slack notification...")
            post_slack_notification(week_label, total_added, sheet_url)
        else:
            print("No qualifying roles found this week — skipping Slack.")

    except Exception as e:
        print(f"\nFATAL ERROR: {e}")
        post_slack_error(str(e))
        raise


if __name__ == "__main__":
    main()
