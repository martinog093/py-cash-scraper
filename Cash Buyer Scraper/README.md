# Cash Buyer Scraper

Scrapes weekly deed filings from **Shelby County TN (Memphis)** and **Bergen County NJ**, identifies cash buyers, scores leads by priority, and outputs a CSV.

---

## Requirements

- Python 3.10 or newer
- Google Chrome is NOT required — Playwright installs its own browser

---

## Setup (one-time)

**1. Create a virtual environment and install dependencies:**

```bash
python -m venv venv
venv\Scripts\activate        # Windows
# OR
source venv/bin/activate     # Mac / Linux

pip install -r requirements.txt
playwright install chromium
```

**2. Create your `.env` file** by copying the example:

```bash
copy .env.example .env        # Windows
```
```bash
cp .env.example .env          # Mac / Linux
```

Then open `.env` and fill in your details — see **Google Sheets setup** and **Email setup** below for exactly what goes in each field:

```
GOOGLE_SHEET_ID=        ← from Google Sheets setup below
EMAIL_SENDER=           ← from Email setup below
EMAIL_PASSWORD=         ← from Email setup below
EMAIL_RECIPIENT=        ← who should receive the weekly report
PROXY_URL=              ← leave blank (only needed outside the US)
```

You can leave any of these blank and run with `--no-email` / `--no-sheets` until you're ready to configure them.

---

## Running the scraper

**Standard weekly run (both markets, last 7 days):**

```bash
python main.py --no-email --no-sheets
```

**Test with a single ZIP code:**

```bash
python main.py --days 30 --zip 38103 --no-email --no-sheets
```

**Full run with email and Google Sheets (once configured):**

```bash
python main.py
```

---

## Output

Results are saved to `output/cash_buyers_YYYY-MM-DD.csv` with these columns:

| Column | Description |
|---|---|
| `market` | Memphis TN or Bergen County NJ |
| `buyer_name` | Name of the cash buyer |
| `seller_name` | Who they bought from |
| `property_address` | Property address |
| `sale_date` | Date deed was recorded |
| `purchase_price` | Sale price (Shelby only; Bergen index does not include price) |
| `deed_type` | Warranty Deed, Quit Claim, Bargain & Sale, etc. |
| `record_number` | Instrument / file number |
| `priority` | Hot / Warm / Standard |
| `times_bought_90d` | How many times this buyer has purchased in the last 90 days |

**Priority scoring:**
- **Hot** — buyer has purchased 2 or more properties in the last 90 days
- **Warm** — LLC or company buyer with purchase price over $150,000
- **Standard** — everything else

Hot leads are sorted to the top of the CSV.

---

## Command options

| Option | Description |
|---|---|
| `--days N` | How many days back to scrape (default: 7) |
| `--zip ZIP` | Scrape one ZIP code only (useful for testing) |
| `--no-email` | Skip email delivery |
| `--no-sheets` | Skip Google Sheets upload |

---

## Logs

Every run writes three log files:

- `logs/run_YYYY-MM-DD.log` — full run log with record counts
- `logs/discarded.log` — records excluded and why (coming in next update)
- `logs/errors.log` — network errors and parse failures (coming in next update)

---

## Google Sheets setup

The service account JSON is already included in `credentials/google_service_account.json` — you don't need to create your own Google Cloud project. Just:

1. Create a blank Google Sheet (https://sheets.google.com)
2. Click **Share** and give edit access to this email address:
   ```
   sheets-writer@cash-buyer-scraper.iam.gserviceaccount.com
   ```
3. Copy the Sheet ID from the URL — it's the long string between `/d/` and `/edit`:
   ```
   https://docs.google.com/spreadsheets/d/THIS_PART_IS_THE_ID/edit
   ```
4. Add it to `.env`:
   ```
   GOOGLE_SHEET_ID=your_sheet_id_here
   ```

The first run writes a header row automatically; every run after that just appends new rows underneath. Confirmed working — tested with live writes before delivery.

---

## Email setup

The scraper uses Gmail SMTP. You need a **Gmail App Password** (your regular Gmail password will not work — Google blocks it for security):

1. Go to your Google Account → Security → turn on **2-Step Verification** (required before App Passwords are available)
2. Go to https://myaccount.google.com/apppasswords → create one for "Mail"
3. Copy the 16-character code into `.env`:
   ```
   EMAIL_SENDER=your@gmail.com
   EMAIL_PASSWORD=xxxx xxxx xxxx xxxx
   EMAIL_RECIPIENT=recipient@email.com
   ```

**First email may land in Spam.** This is normal — Gmail is cautious about a new automated sender the first time. Open the email, click **Not Spam**, and add the sender to your contacts. After that, weekly reports will land in the inbox normally.

Confirmed working — tested with a live send before delivery.
