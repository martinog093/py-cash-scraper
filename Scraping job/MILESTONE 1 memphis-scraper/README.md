# Memphis Daily News Public Records Scraper

This project is a set of separate scripts — one per record type — that each
log into your Memphis Daily News subscriber account, cross-reference filings
against Shelby County property records, and write their own Excel file:

| Script | Record type | Output |
|---|---|---|
| `tax_lien_scraper.py` | Tax Liens & Releases | `output/tax_lien.xlsx` |
| `probate_scraper.py` | Probate Court filings | `output/probate.xlsx` |
| `divorce_scraper.py` | Divorce filings (Circuit + Chancery Court) | `output/divorce.xlsx` |

They share the same `.env` credentials, login/logout handling, and `logs/`
folder — see the **Setup**, **Logging**, and **File Structure** sections
below, which apply to all three.

---

## Running All Three at Once (`main.py`)

```bash
python main.py                       # today's filings, all three scrapers
python main.py 2026-06-10            # a specific date, all three scrapers
python main.py 2026-06-01 2026-06-05  # a date range, all three scrapers
```

This runs Tax Lien, Probate, and Divorce one after another. **There is a
built-in 5-minute wait between each script.** Memphis Daily News rejects a
login that happens too soon after a previous one logs out — without this
wait, the 2nd and 3rd scripts in the chain fail with a "credentials
rejected" error even though the credentials are correct. A full run of all
three takes roughly 10-15 minutes longer than running them individually,
because of these two built-in waits.

If you only need one record type, run that script directly (see its section
below) — no wait is needed since there's no chained login.

---

## Tax Lien Scraper (`tax_lien_scraper.py`)

### What This Does

This script automatically:

1. Logs into your Memphis Daily News subscriber account
2. Navigates to the **Tax Liens & Releases** section for a given date (defaults to today)
3. Opens each individual filing and extracts: debtor/grantee name, property address, instrument number, and recording date
4. Cross-references every record against the **Shelby County Assessor portal** to confirm the person owns property in Shelby County and retrieve their address + parcel ID
5. Compares the address from the Memphis Daily News filing against the Assessor's address for that owner — if they don't match, the original filing address is kept in `unverified_address` for manual review
6. Writes all results to a formatted Excel file with three sheets — **Verified Leads** (owner found in Assessor records and address matches), **Needs Review** (owner found, but address didn't match or there's more than one candidate), and **Discarded** (no property found under that name)

---

## Setup (one-time)

### 1. Make sure Python 3.10 or higher is installed

```bash
python --version
```

If it shows 3.10 or above, you're good. If not, download it from python.org.

### 2. Install dependencies

Open a terminal, navigate to this folder, and run:

```bash
python -m venv venv
```

Then activate the virtual environment:

**Windows (Command Prompt or PowerShell):**
```bash
venv\Scripts\activate
```

**Mac / Linux:**
```bash
source venv/bin/activate
```

You'll know it worked if you see `(venv)` at the start of your terminal prompt. Then install the dependencies:

```bash
pip install -r requirements.txt
```

Then install the browser engine Playwright uses:

```bash
playwright install chromium
```

This downloads a headless version of Chrome (~300 MB, one-time only).

### 3. Add your credentials

Open the `.env` file in this folder. You will see:

```
MDN_USERNAME=your_email@example.com
MDN_PASSWORD=your_password_here
```

Replace the placeholder values with your actual Memphis Daily News login email and password. Save the file.

> **Your credentials never leave your machine.** They are read from `.env` at runtime and are never written to any log file, output file, or sent anywhere. See the Logging section below for how to verify this yourself.

---

## Running the Tax Lien Scraper

### Scrape today's Tax Lien filings

```bash
python tax_lien_scraper.py
```

### Scrape a specific date

```bash
python tax_lien_scraper.py 2026-06-05
```

Replace `2026-06-05` with any date in `YYYY-MM-DD` format.

### Scrape a range of dates (e.g. to catch up on several days at once)

```bash
python tax_lien_scraper.py 2026-06-01 2026-06-05
```

Both dates are inclusive. The script logs in **once**, scrapes every day in the range, and logs out once at the end — all results from every date go into the same `output/tax_lien.xlsx` file.

---

## Output Files

> **Tip:** If `output/tax_lien.xlsx` is open in Excel when the script finishes, Windows won't let it overwrite that file. In that case the script saves the new results as `tax_lien_<timestamp>.xlsx` instead (and says so in `logs/run.log`) so nothing is lost. Just close the file in Excel before your next run to keep using the normal filename.

**Results accumulate across runs.** Each time you run the script, its results are merged into the existing `output/tax_lien.xlsx` rather than replacing it — so running today, then again tomorrow, builds up one combined list. If you re-run a date you already scraped, the new results for that date replace the old ones for the same record (matched by docket number) instead of creating duplicates.

After a successful run you will find:

### `output/tax_lien.xlsx`

A formatted Excel workbook with three sheets:

**Sheet 1 — Verified Leads**

The Assessor's address for this owner matches the address on the MDN filing. High confidence — ready to use.

| Column | Description |
|---|---|
| `filing_date` | Date the lien was recorded |
| `record_type` | "Tax Lien" or "Tax Lien Release" |
| `primary_name` | Debtor (new lien) or Grantee (release) — the property owner |
| `secondary_name` | Blank for Tax Liens |
| `docket_number` | MDN Instrument # |
| `verified_address` | The owner's address as recorded by the Shelby County Assessor |
| `unverified_address` | Always "-" on this sheet |
| `parcel_id` | APN from the Assessor portal |
| `debt_amount` | See note below |
| `status` | "Verified" |
| `discard_reason` | Blank |

**Sheet 2 — Needs Review**

The owner's name *was* found in the Shelby County Assessor's records (so they do own property in Shelby County), but the address from the MDN filing didn't match any property under that name. This can happen because:
- the MDN filing's address belongs to a different party in the document (common on "Tax Lien Release" filings), or
- there's more than one person with this name in the Assessor's records and we can't tell which one filed this lien

If the MDN filing includes a middle name or initial (e.g. "Jawwad A Ahmed"), the script first tries to narrow multiple same-name results down using it — e.g. preferring an Assessor record for "AHMED JAWWAD A" over "AHMED JAWWAD". If that narrows it to a single match, only that one is shown. If two or more candidates are still equally likely, all of them are listed.

Same columns as Sheet 1, except:
- `verified_address` / `parcel_id` — **every** remaining candidate property found under this name, semicolon-separated. Check each against `unverified_address` to judge which (if any) is the right match
- `unverified_address` — the address from the MDN filing, for comparison
- `status` — "Needs Review" (or "Assessor Unavailable" if the Assessor portal couldn't be reached for this record — `verified_address`/`parcel_id` will be blank in that case)
- `discard_reason` — explains why the record landed here

**Sheet 3 — Discarded**

Records where the Assessor portal has **no property at all** under that owner's name (i.e. they don't appear to own property in Shelby County), with the reason noted in the `discard_reason` column.

---

## Note on the Debt Amount Column

The `debt_amount` column will currently be **blank** for all Tax Lien records — the Memphis Daily News filing notice itself does not include the dollar amount owed; that figure only appears inside the scanned legal document attached to each filing.

---

## Probate Scraper (`probate_scraper.py`)

### What This Does

This script automatically:

1. Logs into your Memphis Daily News subscriber account (same credentials as the Tax Lien scraper)
2. Navigates to the **Probate Court** filings for a given date (defaults to today) and collects each filing's docket number (e.g. `PR035936`)
3. Looks up each docket number on the **Shelby County docket report** (prdata.shelbycountytn.gov) to find:
   - The **deceased person's name** (listed as "RE: MATTER" on the docket report)
   - The **petitioner/executor name(s)** who filed the case (listed as "PETITIONER" — there can be more than one)
4. Cross-references the deceased person's name against the **Shelby County Assessor portal** to confirm whether they owned property in Shelby County, and retrieves the property's address + parcel ID
5. Writes all results to a formatted Excel file with the same three-sheet layout as the Tax Lien scraper — **Verified Leads**, **Needs Review**, and **Discarded**

### Running It

```bash
python probate_scraper.py                       # today's filings
python probate_scraper.py 2026-06-10             # a specific date
python probate_scraper.py 2026-06-01 2026-06-05  # a date range (inclusive)
```

Same login/logout, retry, and "results accumulate across runs" / locked-file
fallback behavior described under **Output Files** above for the Tax Lien
scraper applies here too — just for `output/probate.xlsx` instead.

### `output/probate.xlsx`

Same three-sheet format as `tax_lien.xlsx`, with these differences:

| Column | Probate meaning |
|---|---|
| `primary_name` | The deceased person (the subject of the probate case, "RE: MATTER" on the docket report) |
| `secondary_name` | The petitioner(s)/executor(s) who filed the case — useful for skip-tracing the heir. Multiple petitioners are separated by `;` |
| `unverified_address` | Always `-` — Probate filings don't include a property address to compare against, unlike Tax Liens |
| `debt_amount` | Always blank — not applicable to Probate |
| `verified_address` / `parcel_id` | The deceased person's property address/parcel from the Assessor, if found. On "Needs Review" rows, every candidate property is listed, semicolon-separated |

**Sheet 2 — Needs Review** can include two statuses specific to this script:
- **"Assessor Unavailable"** — the Shelby County Assessor portal couldn't be reached for this record
- **"Docket Unavailable"** — the Shelby County docket report (prdata.shelbycountytn.gov) couldn't be reached for this case. `primary_name` and `secondary_name` will be blank in this case, since both come from that report — re-running the same date later will retry it

---

## Divorce Scraper (`divorce_scraper.py`)

### What This Does

This script automatically:

1. Logs into your Memphis Daily News subscriber account (same credentials as the other scrapers)
2. Navigates to both **Court Filings: Circuit** and **Court Filings: Chancery** listings for a given date — these categories contain a mix of case types (contract disputes, personal injury, hospital liens, divorces, etc.)
3. Opens each individual record to check its **Type** field — only records where Type is "Divorce" are processed; all others are silently skipped
4. For each divorce, extracts the **Plaintiff** and **Defendant** names
5. Searches the **Shelby County Assessor portal** for the plaintiff first; if no property is found under the plaintiff, searches the defendant instead
6. Writes all results to `output/divorce.xlsx` with the same three-sheet layout — **Verified Leads**, **Needs Review**, and **Discarded**

### Running It

```bash
python divorce_scraper.py                       # today's filings
python divorce_scraper.py 2026-06-12             # a specific date
python divorce_scraper.py 2026-06-01 2026-06-12  # a date range (inclusive)
```

Same login/logout, retry, and "results accumulate across runs" / locked-file
fallback behavior described under **Output Files** above for the Tax Lien
scraper applies here too — just for `output/divorce.xlsx` instead.

### `output/divorce.xlsx`

Same three-sheet format as the other scrapers, with these differences:

| Column | Divorce meaning |
|---|---|
| `primary_name` | Plaintiff |
| `secondary_name` | Defendant |
| `unverified_address` | Always `-` — divorce filings don't include a property address |
| `debt_amount` | Always blank — not applicable to Divorce |
| `verified_address` / `parcel_id` | Property found under plaintiff or defendant name on the Assessor portal. On "Needs Review" rows, every candidate property is listed, semicolon-separated |

The `discard_reason` column identifies which name the property was found under if it was the defendant (not the plaintiff), and explains the reason for any "Needs Review" or "Discarded" outcome.

---

## Logging

Every run produces two log files in the `logs/` folder:

### `logs/run.log`

A timestamped record of everything the script did:
- Which pages it visited (URLs only — no login credentials)
- How many records were found and processed
- The Assessor lookup result for each record (Verified / Discarded / Unavailable)
- Any errors or warnings

### `logs/discarded.log`

One line per discarded record showing the name, record type, and reason it was discarded (e.g., "No property found under name").

**Regarding your login credentials:** your username and password are never written to any log file. The script only logs actions ("Navigating to login page", "Login successful") — never the values you entered.

**Session handling:** at the end of every run (even if no records were found, or if an error occurs partway through), the script logs out of Memphis Daily News so the session ends immediately. This means you can run the script again right away without getting a "previous session not ended" login error.

You can verify this yourself at any time: open `logs/run.log` in a text editor and press **Ctrl + F**, then search for your username and your password. Neither will appear anywhere in the file.

If something goes wrong and you need help diagnosing it, send me the `run.log` file. It gives me full visibility into where the script failed without exposing any of your account details.

---

## Pre-flight Check

Before running the full scraper for the first time, you can run a quick verification to confirm your Python environment and internet connections are working:

```bash
python test_setup.py
```

---

## File Structure

```
memphis-scraper/
├── .env                      ← your credentials (never shared)
├── tax_lien_scraper.py       ← Tax Lien scraper
├── probate_scraper.py        ← Probate scraper
├── divorce_scraper.py        ← Divorce scraper
├── requirements.txt          ← Python dependencies
├── test_setup.py             ← pre-flight environment check
├── src/
│   ├── auth.py               ← Memphis Daily News login
│   ├── assessor.py           ← Shelby County Assessor lookup
│   ├── docket_lookup.py      ← Shelby County docket report lookup (Probate)
│   ├── output.py             ← Excel writer
│   ├── logging_setup.py      ← shared logging setup
│   └── scrapers/
│       ├── tax_lien.py       ← Tax Lien scraper module
│       ├── probate.py        ← Probate Court listing scraper module
│       └── divorce.py        ← Divorce (Circuit + Chancery) scraper module
├── output/
│   ├── tax_lien.xlsx         ← Tax Lien output (created on first run)
│   ├── probate.xlsx          ← Probate output (created on first run)
│   └── divorce.xlsx          ← Divorce output (created on first run)
└── logs/
    ├── run.log               ← full activity log (shared by all scripts)
    └── discarded.log         ← discarded records log (shared by all scripts)
```

---

## Milestones

| Milestone | Scope | Status |
|---|---|---|
| M1 | Tax Lien scraping + Assessor verification | Complete |
| M2 | Probate scraper + Shelby County docket-report cross-reference | Complete |
| M2 | Divorce scraper (Circuit + Chancery Court Filings) | Complete |
