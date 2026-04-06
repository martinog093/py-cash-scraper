# Cash Buyer Scraper — Project Brief

## Objective

Scrape cash real estate transactions weekly from two markets, output a clean lead list
ready for skip-tracing and CRM import, delivered to Google Sheets + email every Monday.

**Cash indicator definition:**
- A Warranty Deed or Quit Claim Deed with NO corresponding mortgage / deed of trust
  recorded within 30 days of the sale date.

---

## Markets

### Market 1: Bergen County, NJ

**Portal:** `https://bclrs.co.bergen.nj.us/landrecords/`
**Software:** NewVision SearchNG — Angular SPA (NOT classic ASP.NET WebForms)
**Scraper required:** Playwright (JavaScript must execute)

**Critical constraints discovered during portal investigation:**
- **No ZIP code search field exists** in SearchNG. The only geographic filter is
  **Municipality / Town** dropdown (Bergen County has 70 municipalities).
  → Strategy: map the 41 client zip codes to their Bergen municipalities, search
    by municipality instead, and include the full address from the deed image if needed.
- **CAPTCHA likely present** (confirmed in sister-county Morris County instance running
  identical software). Budget for manual solve during development; consider a CAPTCHA
  service (2captcha / capsolver) for production.
- Server may block known datacenter IPs — use a real residential proxy or run locally.
- No bulk download, no public API.

**What the search returns:**
- Grantor (seller), Grantee (buyer), Recording Date, Instrument Number, Document Type
- Consideration Amount (sale price — NJ requires realty transfer tax so it's always present)
- Property address is NOT in the index — only Town / Lot / Block.
  Full address is inside the document image. For production, either open each document
  or accept Town-level granularity.

**Cash detection for Bergen County:**
- After collecting all WD/QC deeds for the period, search for mortgages (MTG document
  type) filed by the same grantee (buyer) within 30 days of the deed date.
- If no mortgage found → cash sale.

**Zip codes provided by client** (map these to Bergen municipalities):
07601, 07602, 07603, 07604, 07605, 07606, 07607, 07608, 07620, 07621, 07624, 07626,
07627, 07628, 07630, 07631, 07632, 07640, 07641, 07642, 07643, 07644, 07645, 07646,
07647, 07648, 07649, 07650, 07652, 07653, 07656, 07657, 07660, 07661, 07662, 07663,
07666, 07670, 07675, 07676, 07677

---

### Market 2: Shelby County, TN (Memphis)

**Portal:** `https://search.register.shelby.tn.us/`
**System:** Legacy PHP — simple stateless POST endpoints, no login required
**Scraper required:** HTTPX (plain HTTP POST) — no Playwright needed

**Primary endpoint:** `POST https://search.register.shelby.tn.us/p2.php`

Search parameters:
```
propzip=38116         # ZIP code filter — works!
startDate=06/01/2026  # MM/DD/YYYY
endDate=06/16/2026
itype2=WD             # Warranty Deed
itype3=QC             # Quit Claim
searchtype=ADDR
```

Instrument type codes:
- `WD` — Warranty Deed
- `QC` — Quit Claim
- `TD` — Deed of Trust (the "mortgage equivalent" in TN)
- `MTG` — Mortgage
- `STR` — Substitute Trustee's Deed (foreclosure — exclude)
- `ALL` — All types

**Result columns (from the `hit_list` HTML table):**
Inst #, Inst Code, Grantor/Debtor, Grantee/Secured Party, Date, Transfer Amount,
**Mortgage Amount**, Street Number, Street Name, City, State, Zip

**Cash detection — shortcut available:**
The `Mortgage Amount` column is already in the p2.php results. If `Mortgage Amount == 0`
for a WD or QC deed, treat as cash sale. This is a fast first-pass filter.
For full accuracy, also POST to p3.php searching for TD filings by the same grantee name
within 30 days (belt-and-suspenders check).

**Detail page** (`pdetail.php?year=YYYY&instnum=NNNNN&db=0&book=**0`):
Adds Parcel ID, Subdivision, Lot, full legal description, execution date vs. recording date.

**CSV download:**
`csvdownload.php` — session-based, must be called in same session as p2.php search.
Can use this instead of HTML parsing if preferred.

**Zip codes to loop:**
38002, 38016, 38017, 38018, 38028, 38053, 38054, 38101, 38103, 38104, 38105, 38106,
38107, 38108, 38109, 38111, 38112, 38113, 38114, 38115, 38116, 38117, 38118, 38119,
38120, 38122, 38125, 38126, 38127, 38128, 38130, 38131, 38132, 38133, 38134, 38135,
38136, 38137, 38138, 38139, 38141

**robots.txt note:** Crawl-delay of 30 seconds applies to `User-agent: *`. The public
search endpoints (p2.php, p3.php) are not explicitly disallowed. Respect the delay.

---

## Output Schema

Single CSV / Google Sheet with these columns:

| Column | Description |
|---|---|
| `market` | "Bergen County NJ" or "Memphis TN" |
| `buyer_name` | Individual or LLC name |
| `entity_type` | "Individual" or "LLC" |
| `property_address` | Address of purchased property |
| `sale_date` | Recording date |
| `purchase_price` | Dollar amount |
| `deed_type` | "Warranty Deed" or "Quit Claim" |
| `record_number` | Instrument / document number |
| `buyer_mailing_address` | If available from deed |
| `times_bought_90d` | How many times buyer appears in last 90 days |
| `priority` | "Hot", "Warm", or "Standard" (see scoring below) |

---

## Priority Scoring

- **Hot**: Buyer appears 2+ times in last 90 days (any deed type)
- **Warm**: LLC buyer + single purchase over $150,000
- **Standard**: Everything else

Entity type detection:
- If buyer name contains "LLC", "INC", "CORP", "TRUST", "LP", "LTD", "HOLDINGS",
  "PROPERTIES", "REALTY", "INVESTMENTS", "GROUP", "VENTURES" → "LLC" (entity)
- Otherwise → "Individual"

---

## Filters

- Minimum purchase price: **$50,000** (drops tax sales and noise)
- Deed types: **Warranty Deed** and **Quit Claim Deed** only
- Cash only: no mortgage / deed of trust within 30 days
- Date range: **last 7 days** by default (weekly run)

---

## Optional Single-Zip Mode

Script must support an optional single ZIP code argument:
```
python main.py --zip 38116          # run only this zip, both markets if applicable
python main.py --zip 07601          # Bergen County only (NJ zip)
python main.py                      # default: full county loop, last 7 days
python main.py --days 14            # override lookback window
```

---

## Automation & Delivery

- **Schedule:** Every Sunday night (cron: `0 2 * * 1` = Monday 02:00)
- **Google Sheets:** Append rows to a shared sheet (gspread + Google Service Account)
- **Email:** Send CSV attachment to configured email address
- **Deduplication:** Within a single run, deduplicate by (buyer_name, property_address).
  Repeat buyers across weeks are KEPT — they are the highest-priority leads.

---

## Buyer History (90-day repeat detection)

Persistent SQLite database (`data/buyer_history.db`) tracks all confirmed cash purchases:
- Table: `purchases(market, buyer_name, property_address, sale_date, purchase_price, record_number)`
- Each run: insert new records, then query last 90 days to compute `times_bought_90d`
- This survives across weekly runs

---

## Technical Stack

| Component | Library |
|---|---|
| Shelby County scraping | `httpx` (plain HTTP POST) |
| Bergen County scraping | `playwright` (Angular SPA) |
| HTML parsing | `beautifulsoup4` |
| Data processing | `pandas` |
| Google Sheets | `gspread` + `google-auth` |
| Email | `smtplib` (Gmail SMTP or SendGrid) |
| Scheduling | System cron |
| Buyer history | `sqlite3` (stdlib) |
| Config | `python-dotenv` |
| Logging | `logging` (stdlib) |

---

## Credentials & Config (`.env`)

```
# Google Sheets
GOOGLE_SHEET_ID=your_sheet_id_here
GOOGLE_CREDENTIALS_FILE=credentials/google_service_account.json

# Email delivery
EMAIL_SENDER=your_gmail@gmail.com
EMAIL_PASSWORD=your_app_password_here
EMAIL_RECIPIENT=client@example.com

# Bergen County (if CAPTCHA service used)
CAPTCHA_API_KEY=

# Optional proxy for Bergen County
PROXY_URL=
```

---

## File Structure

```
cash-buyer-scraper/
├── .env                          # credentials — never commit
├── .gitignore
├── main.py                       # entry point — runs both markets
├── requirements.txt
├── README.md
├── credentials/
│   └── google_service_account.json  # never commit
├── src/
│   ├── __init__.py
│   ├── scrapers/
│   │   ├── __init__.py
│   │   ├── shelby.py             # Shelby County p2.php scraper
│   │   └── bergen.py             # Bergen County SearchNG Playwright scraper
│   ├── cash_filter.py            # cash sale detection logic
│   ├── priority.py               # Hot/Warm/Standard scoring
│   ├── buyer_history.py          # SQLite 90-day history
│   ├── output.py                 # CSV + Google Sheets writer
│   └── email_sender.py           # email delivery
├── data/
│   └── buyer_history.db          # SQLite — persists across runs
├── output/
│   └── cash_buyers_YYYY-MM-DD.csv
└── logs/
    └── run.log
```

---

## Build Order

1. **Shelby County scraper** (`src/scrapers/shelby.py`) — simpler, test first
2. **Cash filter** (`src/cash_filter.py`) — Mortgage Amount == 0 shortcut + TD cross-check
3. **Buyer history** (`src/buyer_history.py`) — SQLite persistence
4. **Priority scoring** (`src/priority.py`)
5. **Output** (`src/output.py`) — CSV + Google Sheets
6. **Email** (`src/email_sender.py`)
7. **Main runner** (`main.py`) — wire everything together for Shelby
8. **Bergen County scraper** (`src/scrapers/bergen.py`) — Playwright, handle CAPTCHA

**Do NOT build Bergen County until Shelby County is end-to-end working.**

---

## Notes

- Bergen County CAPTCHA: if automated CAPTCHA solving is not feasible, the fallback is
  the PropStream API or ATTOM Data API (client to provide credentials).
- Bergen County has no ZIP filter — use municipality names mapped from the provided ZIP
  codes. A ZIP-to-municipality mapping file is needed (`data/bergen_zip_to_town.json`).
- Shelby County `p2.php` returns max 1,000 results per search. If a ZIP has >1,000
  records in the date range (unlikely for 7 days), split by date or instrument type.
- Never use `time.sleep()` for delays — use `asyncio.sleep()` or Playwright's built-in
  waits. For the 30-second crawl delay on Shelby County, use `asyncio.sleep(30)` between
  ZIP code requests.
