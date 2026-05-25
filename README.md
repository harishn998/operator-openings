# Operator Openings — Weekly Job Fetcher

Fetches fresh 3PL / warehouse / ecommerce job postings from JSearch (RapidAPI)
every Monday and writes them to a Google Sheet for Harrison to review on Tuesday.

---

## How it works

1. GitHub Actions triggers every Monday at 8 AM ET
2. Python script calls JSearch API with 12 targeted queries (Canada + US)
3. Results are deduplicated and written to a new tab in the Google Sheet
4. Harrison opens the sheet on Tuesday, picks 4–5 roles, pastes into Beehiiv

---

## One-time setup — follow these steps in order

### Step 1 — Create the Google Sheet

1. Go to sheets.google.com → create a new blank spreadsheet
2. Name it: **Operator Openings — Inbound Newsletter**
3. Copy the Sheet ID from the URL:
   `https://docs.google.com/spreadsheets/d/THIS_IS_YOUR_SHEET_ID/edit`
4. Save it — you'll need it for Step 4

---

### Step 2 — Create a Google Service Account

This lets GitHub Actions write to the sheet without a browser login.

1. Go to console.cloud.google.com
2. Create a new project (or use an existing one) — name it **AMZ Prep Automation**
3. In the left menu: APIs & Services → Enable APIs
4. Search for and enable: **Google Sheets API** and **Google Drive API**
5. Go to: APIs & Services → Credentials → Create Credentials → Service Account
6. Name: `operator-openings-bot` → click Create and Continue → Done
7. Click the service account email → Keys tab → Add Key → Create new key → JSON
8. Download the JSON file — this is your `credentials.json`
9. **Important:** Copy the `client_email` field from the JSON
   (looks like: `operator-openings-bot@your-project.iam.gserviceaccount.com`)

---

### Step 3 — Share the Google Sheet with the service account

1. Open your Google Sheet
2. Click Share (top right)
3. Paste the `client_email` from Step 2 into the share field
4. Set permission to **Editor**
5. Click Send

---

### Step 4 — Add GitHub Secrets

Go to the repo on GitHub → Settings → Secrets and variables → Actions → New repository secret

Add these 3 secrets:

| Secret name | Value |
|---|---|
| `RAPIDAPI_KEY` | Your RapidAPI key from the JSearch dashboard (visible in the screenshot) |
| `GOOGLE_SHEET_ID` | The Sheet ID copied from the URL in Step 1 |
| `GOOGLE_CREDENTIALS_JSON` | The **entire contents** of the JSON file downloaded in Step 2 — paste the whole thing |

---

### Step 5 — Test the workflow manually

1. Go to the repo on GitHub → Actions tab
2. Click **Operator Openings — Weekly Job Fetch**
3. Click **Run workflow** → Run workflow (leave dry_run as false)
4. Watch the logs — should complete in under 2 minutes
5. Open the Google Sheet — you should see a new tab named `Week of YYYY-MM-DD` with job results

---

## File structure

```
operator-openings/
├── .github/
│   └── workflows/
│       └── operator-openings.yml   ← GitHub Actions schedule + config
├── scripts/
│   └── fetch_jobs.py               ← Main script — fetches jobs, writes to sheet
├── requirements.txt                ← Python dependencies
└── README.md                       ← This file
```

---

## API usage

- Free tier: 200 requests/month
- This workflow uses: 12 requests/week × 4 weeks = 48 requests/month
- Remaining buffer: 152 requests/month

---

## Google Sheet structure

Each Monday run creates a new tab named `Week of YYYY-MM-DD` with these columns:

| Column | Description |
|---|---|
| Role Title | Job title |
| Company | Employer name |
| Location | City, State/Province |
| Country | Canada or United States |
| Employment Type | Full-time, Contract, etc. |
| Apply Link | Direct link to apply |
| Source | Always "JSearch / Google for Jobs" |
| Date Fetched | Date the script ran |
| Status | Starts as "Pending Review" — Harrison updates to "Featured" or "Skip" |

---

## Harrison's Tuesday workflow

1. Open the Google Sheet
2. Go to this week's tab (`Week of YYYY-MM-DD`)
3. Scan the list — pick 4–5 roles using these criteria:
   - Mix of Canada and US locations
   - Mix of seniority (1x VP/Director + 2–3x Manager level)
   - Mix of categories (3PL, warehouse, ecommerce, Amazon)
   - Recognisable brands where possible
4. Copy: Role Title + Company + Location + Apply Link
5. Paste into the Operator Openings section in Beehiiv
6. Update the Status column to "Featured" for roles used

---

## Queries being searched each week

| Keyword | Locations |
|---|---|
| 3PL operations manager | Canada, United States |
| Fulfillment director OR head of fulfillment | Canada, United States |
| Warehouse operations manager | Canada, United States |
| Ecommerce operations manager | Canada, United States |
| Amazon channel manager | Canada, United States |
| Supply chain director VP logistics | Canada, United States |
