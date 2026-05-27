"""
Operator Openings — Job Fetcher (Rolo)
Runs every Monday via GitHub Actions.

Blair's brief:
- Remote jobs only (or remote-friendly)
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

# ── Targeted queries — CPG / DTC / Amazon brand roles only ───────────────────
# Each query = 1 API call. We fetch up to 10 per query then filter + rank.
# 10 queries × 4 weeks = 40 calls/month (well within 200 free limit)

QUERIES = [
    # Head-level ecommerce at brands
    ("Head of eCommerce DTC brand",               "United States"),
    ("Head of eCommerce DTC brand",               "Canada"),

    # Amazon marketplace / channel
    ("Head of Amazon Marketplace CPG brand",       "United States"),
    ("Amazon Channel Manager DTC brand remote",    "United States"),

    # VP Supply Chain at consumer brands
    ("VP Supply Chain consumer brand remote",      "United States"),
    ("VP Supply Chain CPG brand",                  "Canada"),

    # Director of eCommerce at brands
    ("Director of eCommerce DTC remote",           "United States"),
    ("Director eCommerce Amazon brand",            "Canada"),

    # Head of global supply chain
    ("Head of Global Supply Chain brand remote",   "United States"),

    # Senior / Director Amazon ops at brands
    ("Senior Director Amazon Operations brand",    "United States"),
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

# ── Filters — what Blair DOES want ───────────────────────────────────────────

# Title must contain at least one of these to pass
TITLE_INCLUDE = [
    "head of ecommerce", "head of e-commerce",
    "head of amazon", "head of marketplace",
    "head of global supply", "head of supply chain",
    "vp of ecommerce", "vp ecommerce",
    "vp supply chain", "vp of supply chain",
    "vice president ecommerce", "vice president supply chain",
    "director of ecommerce", "director ecommerce",
    "director of amazon", "director amazon",
    "director of marketplace", "director marketplace",
    "channel manager", "amazon channel",
    "marketplace manager",
    "senior director", "senior manager ecommerce",
    "senior manager amazon",
    "chief commercial", "chief ecommerce",
    "general manager ecommerce",
    "global ecommerce",
]

# Title or company must NOT contain any of these — blocks 3PLs / logistics / retailers
TITLE_EXCLUDE = [
    "3pl", "3p logistics", "warehouse", "fulfillment center",
    "logistics coordinator", "logistics manager", "freight",
    "dispatcher", "driver", "forklift", "picker", "packer",
    "retail store", "store manager", "branch manager",
    "recruiter", "talent acquisition", "hr manager",
    "software engineer", "data analyst", "accountant",
    "marketing manager",  # too generic — keep only ops/ecomm titles
]

COMPANY_EXCLUDE = [
    # Known 3PLs / logistics operators Blair doesn't want
    "virtualvocations", "solenis", "metro supply chain",
    "247 fulfillment", "north american freight",
    "shipbob", "flexport", "maersk", "fedex", "ups", "dhl",
    "xpo", "ryder", "ceva",
]

# Remote signals in title/location
REMOTE_SIGNALS = [
    "remote", "work from home", "wfh", "hybrid", "anywhere",
    "distributed", "virtual",
]


# ── Scoring — prioritise the coolest roles ────────────────────────────────────

SCORE_BOOST = {
    "head of": 10,
    "vp ": 9,
    "vice president": 9,
    "chief": 8,
    "director": 6,
    "amazon": 5,
    "dtc": 4,
    "cpg": 4,
    "global": 3,
    "remote": 3,
    "senior director": 7,
}

def score_job(job: dict) -> int:
    title   = job["role"].lower()
    company = job["company"].lower()
    loc     = job["location"].lower()
    combined = f"{title} {company} {loc}"
    return sum(v for k, v in SCORE_BOOST.items() if k in combined)


# ── Filters ───────────────────────────────────────────────────────────────────

def passes_title_filter(title: str) -> bool:
    t = title.lower()
    return any(inc in t for inc in TITLE_INCLUDE)

def fails_exclusion(title: str, company: str) -> bool:
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
    today  = datetime.today()
    monday = today - timedelta(days=today.weekday())
    return monday.strftime("Week of %B %-d, %Y")


# ── JSearch ───────────────────────────────────────────────────────────────────

def fetch_jobs(query: str, location: str, max_results: int = 10) -> list[dict]:
    params = {
        "query":       f"{query} in {location}",
        "num_pages":   "1",
        "date_posted": "week",
        "country":     "ca" if location == "Canada" else "us",
        "employment_types": "FULLTIME",
    }
    try:
        resp = requests.get(
            JSEARCH_URL, headers=JSEARCH_HEADERS, params=params, timeout=15
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
    except Exception as e:
        print(f"  ERROR fetching '{query}' / {location}: {e}")
        return []

    results = []
    for job in data[:max_results]:
        city    = job.get("job_city") or ""
        state   = job.get("job_state") or ""
        country = job.get("job_country") or location
        loc_parts = [p for p in [city, state] if p]
        loc_str   = ", ".join(loc_parts) if loc_parts else location

        # Mark remote in location if signals present
        job_title = job.get("job_title", "").strip()
        is_remote = job.get("job_is_remote", False)
        if is_remote or is_remote_friendly(job_title, loc_str):
            if "remote" not in loc_str.lower():
                loc_str = f"Remote ({loc_str})" if loc_str else "Remote"

        results.append({
            "role":     job_title,
            "company":  job.get("employer_name", "").strip(),
            "location": loc_str,
            "country":  country,
            "type":     job.get("job_employment_type", "FULLTIME").replace("_", " ").title(),
            "link":     job.get("job_apply_link") or job.get("job_google_link", ""),
            "source":   "JSearch / Google for Jobs",
            "date":     datetime.today().strftime("%Y-%m-%d"),
            "status":   "Pending Review",
        })
    return results


# ── Google Sheets ─────────────────────────────────────────────────────────────

def get_workbook():
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
    if worksheet.row_values(1) != SHEET_HEADERS:
        worksheet.insert_row(SHEET_HEADERS, index=1)
        worksheet.freeze(rows=1)
        worksheet.format("A1:I1", {
            "textFormat": {"bold": True},
            "backgroundColor": {"red": 0.13, "green": 0.31, "blue": 0.55},
        })
        print("  Header row written and formatted.")


def get_or_create_tab(workbook, week_label: str):
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
                "type": "plain_text",
                "text": "📋  Open Job Opportunities for Inbound Newsletter — Sheet Updated",
                # Spreadsheet renamed to: Open Job Opportunities - Inbound Newsletter
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
                    f"*Filter:* Remote · Senior/VP/Head/Director · DTC & Amazon brands\n"
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
                "type": "button",
                "text": {"type": "plain_text", "text": "Open Sheet", "emoji": True},
                "url": sheet_url,
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
        workbook        = get_workbook()
        ws, _           = get_or_create_tab(workbook, week_label)
        ensure_headers(ws)

        all_rows        = ws.get_all_values()
        existing_links  = {r[5] for r in all_rows[1:] if len(r) > 5}

        # ── Fetch all candidates across all queries ───────────────────────────
        all_candidates = []
        for query, location in QUERIES:
            print(f"\nFetching: '{query}' | {location}")
            jobs = fetch_jobs(query, location, max_results=10)
            print(f"  Raw results: {len(jobs)}")

            for job in jobs:
                # Skip duplicates already in sheet
                if job["link"] in existing_links:
                    continue

                # Skip already collected this run (by link)
                if any(c["link"] == job["link"] for c in all_candidates):
                    continue

                # Apply Blair's filters
                if not passes_title_filter(job["role"]):
                    print(f"  SKIP (title filter): {job['role']}")
                    continue

                if fails_exclusion(job["role"], job["company"]):
                    print(f"  SKIP (exclusion): {job['role']} @ {job['company']}")
                    continue

                # Score it
                job["_score"] = score_job(job)
                all_candidates.append(job)
                print(f"  ✓ PASS (score {job['_score']}): {job['role']} @ {job['company']}")

        # ── Sort by score — best roles first ─────────────────────────────────
        all_candidates.sort(key=lambda j: j["_score"], reverse=True)

        # ── Take top MAX_JOBS_PER_WEEK ────────────────────────────────────────
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
            print("No qualifying roles found this week — skipping Slack notification.")

    except Exception as e:
        print(f"\nFATAL ERROR: {e}")
        post_slack_error(str(e))
        raise


if __name__ == "__main__":
    main()
