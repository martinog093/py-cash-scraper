# Client Demo — Shelby County Assessor Lookup + Excel Output

This demo shows two core components of the Memphis Scraper working live:

1. **Assessor Lookup** — queries the Shelby County Assessor portal by owner name and returns a verified property address + parcel ID
2. **Excel Output** — writes the results into a formatted spreadsheet with Verified and Discarded sheets

> The third component (Memphis Daily News login + tax lien scraping) requires subscriber credentials and is not shown here.

---

## Before You Start

Open these 3 URLs in your browser — these are the live Assessor search results the script will read from:

**Robert Williams**
```
https://assessormelvinburgess.com/realPropertyDetails?StreetNumber=&StreetName=&FirstName=Robert&LastName=Williams&ParcelID=&Business=&active=owner&Page=property
```

**Michael Brown**
```
https://assessormelvinburgess.com/realPropertyDetails?StreetNumber=&StreetName=&FirstName=Michael&LastName=Brown&ParcelID=&Business=&active=owner&Page=property
```

**Patricia Jones**
```
https://assessormelvinburgess.com/realPropertyDetails?StreetNumber=&StreetName=&FirstName=Patricia&LastName=Jones&ParcelID=&Business=&active=owner&Page=property
```

Keep these open in the browser. You will verify the script output matches what the website shows.

---

## Step 1 — Navigate to the project folder

**Windows (Command Prompt or PowerShell):**
```bash
cd "C:\Users\YourName\Desktop\memphis-scraper"
```

**Mac / Linux:**
```bash
cd ~/Desktop/Upwork\ Gig/Scraping\ job/MILESTONE\ 1\ memphis-scraper
```

---

## Step 2 — Activate the Python environment

**Windows (Command Prompt or PowerShell):**
```bash
..\venv\Scripts\activate
```

**Mac / Linux:**
```bash
source ../venv/bin/activate
```

---

## Step 3 — Run a single name lookup (Assessor only)

This runs just the assessor lookup for one name and prints the result directly to the console.

```bash
python -c "from src.assessor import search_by_name; print(search_by_name('Robert Williams'))"
```

**Expected console output:**
```
  SOURCE (Assessor) --> https://assessormelvinburgess.com/realPropertyDetails?...FirstName=Robert&LastName=Williams...
{'verified_address': '57 GEORGIA AVE # 304', 'parcel_id': '002088 A00009'}
```

Compare the address and parcel ID directly against the browser tab for Robert Williams.

---

## Step 4 — Run all 3 lookups and write to Excel

This runs all 3 names through the assessor, then feeds the results into the output module to generate an Excel file.

```bash
python demo_output.py
```

**Expected console output:**
```
Looking up: Robert Williams
  SOURCE (Assessor) --> https://assessormelvinburgess.com/realPropertyDetails?...FirstName=Robert&LastName=Williams...
  VERIFIED  →  57 GEORGIA AVE # 304  |  parcel 002088 A00009

Looking up: Michael Brown
  SOURCE (Assessor) --> https://assessormelvinburgess.com/realPropertyDetails?...FirstName=Michael&LastName=Brown...
  VERIFIED  →  1069 TRIGG AVE  |  parcel 026033 00001

Looking up: Patricia Jones
  SOURCE (Assessor) --> https://assessormelvinburgess.com/realPropertyDetails?...FirstName=Patricia&LastName=Jones...
  VERIFIED  →  0 KINGS ALY  |  parcel 007021 00030

Output written to output/demo_verified_leads.xlsx — 3 verified, 0 discarded

Done. Open: output/demo_verified_leads.xlsx
```

---

## Step 5 — Open the Excel file

**Windows (Command Prompt or PowerShell):**
```bash
start output\demo_verified_leads.xlsx
```

**Mac:**
```bash
open output/demo_verified_leads.xlsx
```

**Linux:**
```bash
xdg-open output/demo_verified_leads.xlsx
```

Or open it manually from `MILESTONE 1 memphis-scraper/output/demo_verified_leads.xlsx`.

**What you will see:**

Sheet 1 — **Verified Leads**

| filing_date | record_type | primary_name     | docket_number | verified_address    | parcel_id      | debt_amount | status   |
|-------------|-------------|------------------|---------------|---------------------|----------------|-------------|----------|
| 2026-06-05  | Tax Lien    | Robert Williams  | CT-2026-001   | 57 GEORGIA AVE # 304 | 002088 A00009 | $4,200.00   | Verified |
| 2026-06-05  | Tax Lien    | Michael Brown    | CT-2026-002   | 1069 TRIGG AVE      | 026033 00001   | $1,850.00   | Verified |
| 2026-06-05  | Tax Lien    | Patricia Jones   | CT-2026-003   | 0 KINGS ALY         | 007021 00030   | $9,100.00   | Verified |

Sheet 2 — **Discarded** (empty in this demo — all 3 were matched)

---

## What This Demonstrates

| Component | Status | Notes |
|---|---|---|
| Shelby County Assessor lookup | Working | Queries live portal, returns real property data |
| Address + Parcel ID extraction | Working | Parsed directly from the live results table |
| Excel output with two sheets | Working | Formatted headers, frozen row, auto-sized columns |
| Memphis Daily News login + scraping | Pending | Requires subscriber credentials |

---

## To Complete Milestone 1

The missing piece is the Memphis Daily News login and tax lien scraping.
With subscriber credentials, `python main.py` runs the full pipeline end-to-end:

```
Login → Scrape tax liens → Verify each owner on Assessor → Write Excel
```

No other changes to the code are needed — the credential handoff is the only remaining step.
