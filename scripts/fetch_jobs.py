"""
Operator Openings — Job Fetcher
Runs every Monday via GitHub Actions.
Fetches 3PL / warehouse / ecommerce jobs from JSearch API (RapidAPI),
writes results to a Google Sheet (newest tab always first),
then posts a Slack notification to #newsletter tagging Harrison.
"""

import os
import json
import requests
import gspread
from datetime import datetime, timedelta
from google.oauth2.service_account import Credentials

# ── Config ────────────────────────────────────────────────────────────────────

RAPIDAPI_KEY   = os.environ["RAPIDAPI_KEY"]
SHEET_ID       = os.environ["GOOGLE_SHEET_ID"]
SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]

SLACK_CHANNEL  = "C0B0SNVK84C"          # #newsletter
HARRISON_ID    = "U0947B880H1"           # Harrison McIntyre-Miller

JSEARCH_URL    = "https://jsearch.p.rapidapi.com/search"
JSEARCH_HEADERS = {
    "X-RapidAPI-Key":  RAPIDAPI_KEY,
    "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
    "Content-Type":    "application/json",
}

# 12 queries — 6 keyword groups × 2 locations
# 12 calls/week × 4 weeks = 48/month (well within 200 free limit)
QUERIES = [
    ("3PL operations manager",                      "Canada"),
    ("3PL operations manager",                      "United States"),
    ("fulfillment director OR head of fulfillment", "Canada"),
    ("fulfillment director OR head of fulfillment", "United States"),
    ("warehouse operations manager",                "Canada"),
    ("warehouse operations manager",                "United States"),
    ("ecommerce operations manager",                "Canada"),
    ("ecommerce operations manager",                "United States"),
    ("Amazon channel manager",                      "Canada"),
    ("Amazon channel manager",                      "United States"),
    ("supply chain director VP logistics",          "Canada"),
    ("supply chain director VP logistics",          "United States"),
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

# ── Week label ────────────────────────────────────────────────────────────────

def get_week_label() -> str:
    """
    Returns a human-readable week label.
    Format: 'Week of May 26, 2026'
    Tab is named after the Monday the script runs.
    """
    today = datetime.today()
    # Snap to nearest Monday (the script runs on Monday via cron)
    monday = today - timedelta(days=today.weekday())
    return monday.strftime("Week of %B %-d, %Y")


# ── JSearch ───────────────────────────────────────────────────────────────────

def fetch_jobs(query: str, location: str, max_results: int = 5) -> list[dict]:
    """Call JSearch and return a list of normalised job dicts."""
    params = {
        "query":       f"{query} in {location}",
        "num_pages":   "1",
        "date_posted": "week",
        "country":     "ca" if location == "Canada" else "us",
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

        results.append({
            "role":    job.get("job_title", "").strip(),
            "company": job.get("employer_name", "").strip(),
            "location": loc_str,
            "country":  country,
            "type":     job.get("job_employment_type", "").replace("_", " ").title(),
            "link":     job.get("job_apply_link") or job.get("job_google_link", ""),
            "source":   "JSearch / Google for Jobs",
            "date":     datetime.today().strftime("%Y-%m-%d"),
            "status":   "Pending Review",
        })
    return results


# ── Google Sheets ─────────────────────────────────────────────────────────────

def get_workbook():
    """Authenticate with Google Sheets via service account JSON env var."""
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
    """Write and freeze header row if not already present."""
    if worksheet.row_values(1) != SHEET_HEADERS:
        worksheet.insert_row(SHEET_HEADERS, index=1)
        worksheet.freeze(rows=1)
        # Bold + background colour for header row
        worksheet.format("A1:I1", {
            "textFormat": {"bold": True},
            "backgroundColor": {"red": 0.13, "green": 0.31, "blue": 0.55},
        })
        print("  Header row written and formatted.")


def get_or_create_tab(workbook, week_label: str):
    """
    Return the worksheet for this week.
    If it doesn't exist, create it and move it to position 0
    so the newest tab always appears first (leftmost).
    """
    try:
        ws = workbook.worksheet(week_label)
        print(f"  Found existing tab: '{week_label}'")
        return ws, False   # (worksheet, is_new)
    except gspread.WorksheetNotFound:
        ws = workbook.add_worksheet(
            title=week_label,
            rows=200,
            cols=len(SHEET_HEADERS),
        )
        # Move the new tab to position 0 — newest always first
        workbook.reorder_worksheets([ws] + [
            s for s in workbook.worksheets() if s.id != ws.id
        ])
        print(f"  Created new tab: '{week_label}' (moved to first position)")
        return ws, True    # (worksheet, is_new)


# ── Slack ─────────────────────────────────────────────────────────────────────

def post_slack_notification(week_label: str, total_added: int, sheet_url: str):
    """
    Post a Slack Block Kit message to #newsletter tagging Harrison.
    Fires only when jobs have been successfully added.
    """
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "📋  Operator Openings — Sheet Updated",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"Hey <@{HARRISON_ID}> — this week's job listings are ready for review.\n\n"
                    f"*Week:* {week_label}\n"
                    f"*New listings added:* {total_added}\n"
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
                    "• Open the sheet\n"
                    "• Pick 4–5 roles (mix CA + US, mix seniority)\n"
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
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Fetched automatically via GitHub Actions · {datetime.today().strftime('%A %d %B %Y')}",
                }
            ],
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
            "text":    f"Operator Openings sheet updated — {total_added} new listings for {week_label}",
        },
        timeout=10,
    )

    result = resp.json()
    if result.get("ok"):
        print(f"  Slack notification sent to #newsletter (tagged Harrison)")
    else:
        print(f"  Slack ERROR: {result.get('error', 'unknown error')}")


def post_slack_error(error_msg: str):
    """Post a simple error alert to #newsletter if the script fails."""
    requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={
            "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
            "Content-Type":  "application/json",
        },
        json={
            "channel": SLACK_CHANNEL,
            "text": (
                f"⚠️ <@{HARRISON_ID}> — Operator Openings job fetch *failed* this week.\n"
                f"Error: `{error_msg}`\n"
                f"Check GitHub Actions logs for details."
            ),
        },
        timeout=10,
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{'='*60}")
    print(f"  Operator Openings — Job Fetch Run")
    print(f"  {datetime.today().strftime('%A %d %B %Y')}")
    print(f"{'='*60}\n")

    week_label = get_week_label()
    sheet_url  = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}"

    try:
        # ── Connect to Google Sheets ──────────────────────────────────────────
        print("Connecting to Google Sheets...")
        workbook = get_workbook()
        ws, is_new = get_or_create_tab(workbook, week_label)
        ensure_headers(ws)

        # Get existing apply links to prevent duplicates
        all_rows       = ws.get_all_values()
        existing_links = {r[5] for r in all_rows[1:] if len(r) > 5}

        # ── Fetch and write jobs ──────────────────────────────────────────────
        total_added = 0
        for query, location in QUERIES:
            print(f"\nFetching: '{query}' | {location}")
            jobs = fetch_jobs(query, location, max_results=5)
            print(f"  Found {len(jobs)} jobs")

            for job in jobs:
                if job["link"] in existing_links:
                    print(f"  SKIP (duplicate): {job['role']} @ {job['company']}")
                    continue

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
        print(f"  DONE — {total_added} new jobs added to '{week_label}'")
        print(f"  Sheet: {sheet_url}")
        print(f"{'='*60}\n")

        # ── Slack notification ────────────────────────────────────────────────
        if total_added > 0:
            print("Sending Slack notification...")
            post_slack_notification(week_label, total_added, sheet_url)
        else:
            print("No new jobs added — skipping Slack notification.")

    except Exception as e:
        print(f"\nFATAL ERROR: {e}")
        post_slack_error(str(e))
        raise


if __name__ == "__main__":
    main()
