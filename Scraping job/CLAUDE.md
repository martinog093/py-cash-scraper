# Claude Code Prompt: Memphis Daily News Public Records Scraper

## Project Overview

Build a Python-based web scraping system that:
1. Logs into **Memphis Daily News** (memphisdailynews.com) using subscriber credentials
2. Extracts public records across three filing categories (Divorce, Probate, Tax Liens)
3. Cross-references each record against the **Shelby County Assessor portal** (assessormelvinburgess.com) to verify property ownership
4. Outputs a single unified CSV/Excel file with all verified leads

This is a fixed-price project ($120) broken into 3 milestones. Build and test each milestone independently before moving to the next.

---

## Technical Stack

- **Language:** Python 3.10+
- **Primary scraping tool:** Playwright (handles ASP.NET session/VIEWSTATE on Memphis Daily News)
- **Secondary scraping:** Playwright or HTTPX for Shelby County Assessor (modern Node.js/Express app, clean endpoints)
- **Output:** pandas + openpyxl for CSV/Excel generation
- **Credentials:** python-dotenv — load from `.env` file, NEVER hardcode
- **Logging:** Python `logging` module — log all discarded records with reasons

---

## Credentials & Configuration

Create a `.env` file (never commit to git):

```
MDN_USERNAME=your_username_here
MDN_PASSWORD=your_password_here
```

Load at runtime:
```python
from dotenv import load_dotenv
import os

load_dotenv()
username = os.getenv("MDN_USERNAME")
password = os.getenv("MDN_PASSWORD")
```

If `.env` is missing or credentials are empty, the script must exit with a clear error message:
```
ERROR: Credentials not found. Please create a .env file with MDN_USERNAME and MDN_PASSWORD.
```

---

## Memphis Daily News — Login Flow

The site is ASP.NET WebForms. The login form uses hidden fields `__VIEWSTATE` and `__EVENTVALIDATION` that must be extracted from the page HTML before submitting the login POST request.

**Login endpoint:** `https://www.memphisdailynews.com/Login.aspx`

**Form fields to submit:**
- `ctl00$ctl00$LoginUserTextBox` — username
- `ctl00$ctl00$LoginPassTextBox` — password
- `ctl00$ctl00$LoginButton` — submit trigger
- `__VIEWSTATE` — extract from page
- `__EVENTVALIDATION` — extract from page
- `ctl00$ctl00$KeepLoginCheckBox` — set to `on`

**Implementation approach:**
Use Playwright to navigate to login page, fill the form fields, and click the login button. Verify login success by checking for a logout link or absence of the login form after navigation.

Maintain the authenticated Playwright browser context for all subsequent scraping — do NOT create a new context per request.

---

## Shelby County Assessor Portal — Search Flow

**Base URL:** `https://assessormelvinburgess.com`
**Property Search:** `/propertySearch`

This is a modern Express/Node.js app — no VIEWSTATE. Use name search to look up ownership.

For each search:
- Input: owner name (from Memphis Daily News record)
- Output: property address + parcel ID (APN) if found
- If no match: discard the record, log reason

---

## Output Schema

All results go into a **single unified file**: `output/verified_leads.xlsx`

| Column | Description |
|---|---|
| `filing_date` | Date of the filing |
| `record_type` | "Divorce", "Probate", or "Tax Lien" |
| `primary_name` | Plaintiff (Divorce), Deceased (Probate), or Owner (Tax Lien) |
| `secondary_name` | Defendant (Divorce), Petitioner/Executor (Probate), blank for Tax Lien |
| `docket_number` | Court docket number |
| `verified_address` | Property address confirmed via Assessor portal |
| `parcel_id` | APN from Assessor portal |
| `debt_amount` | Dollar amount (Tax Lien only, blank otherwise) |
| `status` | "Verified" or "Discarded" |
| `discard_reason` | If discarded, why (e.g., "No property found under name") |

---

---

## MILESTONE 1 — Category C: Tax Liens

**Budget:** $40
**Goal:** Scrape Tax Lien filings, cross-reference with Assessor portal, output verified rows.

### What to build:

**1. Login module** (`src/auth.py`)
- Function: `login(playwright_context) -> authenticated_page`
- Load credentials from `.env`
- Navigate to login page, extract VIEWSTATE tokens, submit form
- Verify login success
- Return authenticated Playwright page object

**2. Tax Lien scraper** (`src/scrapers/tax_lien.py`)
- Navigate to Memphis Daily News public records section
- Filter/navigate to Tax Lien category
- For each record, extract:
  - Filing date
  - Owner name
  - Property address (as listed in the notice)
  - Tax amount owed (if explicitly stated in the notice text)
  - Docket number
- Return list of dicts

**3. Assessor verifier** (`src/assessor.py`)
- Function: `verify_ownership(name: str, address: str) -> dict | None`
- Search the Shelby County Assessor portal by owner name
- If address from MDN matches the Assessor record: confirm ownership
- Extract and return: `verified_address`, `parcel_id`
- If no match: return `None`

**4. Output writer** (`src/output.py`)
- Function: `write_output(records: list, filepath: str)`
- Accept list of record dicts
- Write to Excel using openpyxl/pandas
- Include all columns from the output schema above

**5. Main runner** (`main.py`)
- Wire everything together for M1:
```
login → scrape tax liens → for each: verify ownership → write output
```
- Log discarded records to `logs/discarded.log`

### Acceptance criteria for M1:
- Script runs end-to-end without manual intervention
- Successfully logs into Memphis Daily News
- Extracts at least the fields listed above from Tax Lien records
- Correctly queries Shelby County Assessor for each record
- Outputs verified records to Excel
- Discarded records logged with reason
- No credentials in source code

---

## MILESTONE 2 — Categories A & B: Divorce + Probate

**Budget:** $50
**Goal:** Add Divorce and Probate scrapers with name-only Assessor lookups (no address in source data).

### What to build:

**6. Divorce scraper** (`src/scrapers/divorce.py`)
- Navigate to Divorce Filings category on Memphis Daily News
- For each record, extract:
  - Filing date
  - Plaintiff name
  - Defendant name
  - Docket number
- **Note:** No address in the source data — name-only lookup on Assessor portal

**Logic:**
```
for each divorce filing:
    result = assessor.search_by_name(plaintiff_name)
    if result:
        save row with verified_address and parcel_id
    else:
        result = assessor.search_by_name(defendant_name)
        if result:
            save row
        else:
            discard, log "No property found under plaintiff or defendant name"
```

**7. Probate scraper** (`src/scrapers/probate.py`)
- Navigate to Probate Court Filings category
- For each record, extract:
  - Filing date
  - Deceased person's name
  - Petitioner/Executor/Personal Representative name
  - Docket number

**Logic:**
```
for each probate filing:
    result = assessor.search_by_name(deceased_name)
    if result:
        save row — keep petitioner name in secondary_name column
        (petitioner will be used for skip-tracing the heir)
    else:
        discard, log "No property found under deceased name"
```

**8. Update `src/assessor.py`**
- Add function: `search_by_name(name: str) -> dict | None`
- Name-only search (no address to cross-reference)
- Return first matching result with `verified_address` and `parcel_id`
- If multiple results returned: log warning, take first match, flag in output

**9. Update `main.py`**
- Add M2 categories to the pipeline:
```
login → scrape tax liens + divorce + probate → verify all → unified output
```
- All three categories write to the same `verified_leads.xlsx`

### Acceptance criteria for M2:
- Divorce scraper extracts plaintiff, defendant, docket correctly
- Probate scraper extracts deceased name and petitioner/executor correctly
- Name-only Assessor lookup working for both categories
- All three record types appear in unified output file
- Petitioner name correctly preserved in `secondary_name` for Probate records

---

## MILESTONE 3 — Unified Output, Error Handling, Delivery

**Budget:** $30
**Goal:** Polish, harden, and deliver production-ready script.

### What to build:

**10. Robust error handling**
- Wrap all network calls in try/except
- If Memphis Daily News login fails: exit with clear error message
- If Assessor portal is unreachable: log warning, mark records as `"Assessor Unavailable"` in status column, continue
- If a record page fails to load: log and skip, do not crash the whole run
- Retry logic: 3 retries with exponential backoff on network errors

**11. Logging system** (`logs/`)
- `logs/run.log` — timestamped run log (start time, records processed, verified count, discard count)
- `logs/discarded.log` — one line per discarded record with name, category, and reason
- `logs/errors.log` — network errors, parse failures, unexpected HTML structure

**12. Output finalization**
- Final `output/verified_leads.xlsx` with all columns
- Column headers auto-formatted (bold, frozen top row)
- Sheet named "Verified Leads"
- Second sheet named "Discarded" with discarded records + reasons
- Summary row at bottom: total verified, total discarded, run date

**13. README.md**
Write a clean README with:
- Requirements (`pip install -r requirements.txt`)
- `.env` setup instructions
- How to run: `python main.py`
- What the output files contain
- How to adjust date range for scraping

**14. `requirements.txt`**
Pin all dependencies with versions:
```
playwright==1.x.x
pandas==2.x.x
openpyxl==3.x.x
python-dotenv==1.x.x
httpx==0.x.x  (if used)
```

### Acceptance criteria for M3:
- Script handles network failures gracefully without crashing
- All three milestones integrated into single `python main.py` run
- Output Excel has both "Verified Leads" and "Discarded" sheets
- README is clear enough for a non-technical person to set up and run
- No hardcoded credentials anywhere in codebase
- `requirements.txt` present and complete

---

## Project File Structure

```
memphis-scraper/
├── .env                    # credentials — DO NOT COMMIT
├── .gitignore              # include .env
├── main.py                 # entry point
├── requirements.txt
├── README.md
├── src/
│   ├── auth.py             # Memphis Daily News login
│   ├── assessor.py         # Shelby County Assessor portal
│   ├── output.py           # Excel/CSV writer
│   └── scrapers/
│       ├── tax_lien.py     # Category C
│       ├── divorce.py      # Category A
│       └── probate.py      # Category B
├── output/
│   └── verified_leads.xlsx # final output
└── logs/
    ├── run.log
    ├── discarded.log
    └── errors.log
```

---

## Important Notes for Claude Code

- **Start with M1 only.** Do not build M2 or M3 until M1 is tested and working end-to-end.
- **Test the Assessor portal first** before writing the full pipeline. Run a simple name search manually via Playwright and inspect the response structure before building `assessor.py`.
- **Memphis Daily News navigation:** After login, public records are accessible at `/public-records/YYYY/Mon/DD/`. Use today's date or allow a date range parameter.
- **The Assessor portal search** is at `https://assessormelvinburgess.com/propertySearch` — inspect the network requests in the browser to find the actual API endpoint the search form uses (likely a POST or GET to a `/search` or `/api/search` endpoint).
- **Never use `time.sleep()` for delays** — use Playwright's built-in `wait_for_load_state()` and `wait_for_selector()` instead.
- **Handle pagination** — Memphis Daily News record listings may span multiple pages. Detect and follow pagination links.