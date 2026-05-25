"""
Operator Openings — Job Fetcher
Runs every Monday via GitHub Actions.
Fetches 3PL / warehouse / ecommerce jobs from JSearch API (RapidAPI)
and writes results to a Google Sheet for Harrison to review on Tuesday.
"""

import os
import json
import requests
import gspread
from datetime import datetime
from google.oauth2.service_account import Credentials

# ── Config ────────────────────────────────────────────────────────────────────

RAPIDAPI_KEY = os.environ["RAPIDAPI_KEY"]
SHEET_ID     = os.environ["GOOGLE_SHEET_ID"]

JSEARCH_URL  = "https://jsearch.p.rapidapi.com/search"
HEADERS      = {
    "X-RapidAPI-Key":  RAPIDAPI_KEY,
    "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
    "Content-Type":    "application/json",
}

# 8 queries — 4 keyword groups × 2 locations
# Each query = 1 API call. 8 calls/week = 32/month (well within 200 free limit)
QUERIES = [
    ("3PL operations manager",                  "Canada"),
    ("3PL operations manager",                  "United States"),
    ("fulfillment director OR head of fulfillment", "Canada"),
    ("fulfillment director OR head of fulfillment", "United States"),
    ("warehouse operations manager",            "Canada"),
    ("warehouse operations manager",            "United States"),
    ("ecommerce operations manager",            "Canada"),
    ("ecommerce operations manager",            "United States"),
    ("Amazon channel manager",                  "Canada"),
    ("Amazon channel manager",                  "United States"),
    ("supply chain director VP logistics",      "Canada"),
    ("supply chain director VP logistics",      "United States"),
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

# ── Helpers ───────────────────────────────────────────────────────────────────

def fetch_jobs(query: str, location: str, max_results: int = 5) -> list[dict]:
    """Call JSearch and return a list of normalised job dicts."""
    params = {
        "query":       f"{query} in {location}",
        "num_pages":   "1",
        "date_posted": "week",       # only jobs posted in the last 7 days
        "country":     "ca" if location == "Canada" else "us",
    }
    try:
        resp = requests.get(JSEARCH_URL, headers=HEADERS, params=params, timeout=15)
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
            "role":     job.get("job_title", "").strip(),
            "company":  job.get("employer_name", "").strip(),
            "location": loc_str,
            "country":  country,
            "type":     job.get("job_employment_type", "").replace("_", " ").title(),
            "link":     job.get("job_apply_link") or job.get("job_google_link", ""),
            "source":   "JSearch / Google for Jobs",
            "date":     datetime.today().strftime("%Y-%m-%d"),
            "status":   "Pending Review",
        })
    return results


def get_sheet():
    """Authenticate with Google Sheets using service account JSON from env var."""
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
    """Write header row if sheet is empty."""
    if worksheet.row_values(1) != SHEET_HEADERS:
        worksheet.insert_row(SHEET_HEADERS, index=1)
        # Freeze header row
        worksheet.freeze(rows=1)
        print("  Header row written.")


def row_exists(existing_links: set, link: str) -> bool:
    """Avoid duplicate entries based on apply link."""
    return link in existing_links


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{'='*60}")
    print(f"  Operator Openings — Job Fetch Run")
    print(f"  {datetime.today().strftime('%A %d %B %Y')}")
    print(f"{'='*60}\n")

    # Connect to Google Sheet
    print("Connecting to Google Sheets...")
    workbook  = get_sheet()

    # Use a tab named after the current week (e.g. "Week of 2026-05-26")
    week_label = f"Week of {datetime.today().strftime('%Y-%m-%d')}"
    try:
        ws = workbook.worksheet(week_label)
        print(f"  Found existing tab: {week_label}")
    except gspread.WorksheetNotFound:
        ws = workbook.add_worksheet(title=week_label, rows=200, cols=len(SHEET_HEADERS))
        print(f"  Created new tab: {week_label}")

    ensure_headers(ws)

    # Get existing apply links to prevent duplicates
    all_rows      = ws.get_all_values()
    existing_links = {r[5] for r in all_rows[1:] if len(r) > 5}

    # Fetch and write jobs
    total_added = 0
    for query, location in QUERIES:
        print(f"\nFetching: '{query}' | {location}")
        jobs = fetch_jobs(query, location, max_results=5)
        print(f"  Found {len(jobs)} jobs")

        for job in jobs:
            if row_exists(existing_links, job["link"]):
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
    print(f"  DONE — {total_added} new jobs added to tab '{week_label}'")
    print(f"  Sheet ID: {SHEET_ID}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
