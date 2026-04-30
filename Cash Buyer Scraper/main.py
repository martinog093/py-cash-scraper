"""
Cash Buyer Scraper — main entry point.

Usage:
    python main.py [--days N] [--zip ZIP_CODE] [--no-email] [--no-sheets]

Options:
    --days N        Number of days back to scrape (default: 7)
    --zip ZIP       Scrape a single ZIP code only (Shelby) or town only (Bergen)
    --no-email      Skip email delivery
    --no-sheets     Skip Google Sheets upload

The script runs:
  1. Shelby County (Memphis TN) — httpx POST to p2.php, cash filter via p3.php,
     then a live Assessor portal lookup (assessormelvinburgess.com) per confirmed
     cash sale for deed_url/assessor_url/owner cross-check (Shelby only)
  2. Bergen County (Bergen County NJ) — Playwright on BrowserView SPA (single session for scrape + cash filter)
  3. SQLite buyer history update
  4. Priority scoring
  4b. Remarks generation (flip detection, Assessor lag/mismatch notes, Hot-buyer notes)
  5. CSV output  → output/cash_buyers_YYYY-MM-DD.csv
  6. Google Sheets append
  7. Email delivery
"""

import argparse
import logging
import os
import sys
from datetime import date

from dotenv import load_dotenv

load_dotenv()

# ── Logging setup ────────────────────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)
os.makedirs("output", exist_ok=True)
os.makedirs("data", exist_ok=True)

# Windows consoles default to cp1252, which can't encode some Unicode
# characters in log messages. Force UTF-8 with a safe fallback so logging
# never crashes the run regardless of platform/console codepage.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.FileHandler(f"logs/run_{date.today().isoformat()}.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Cash Buyer Scraper")
    parser.add_argument("--days", type=int, default=7, help="Days back to scrape (default: 7)")
    parser.add_argument("--zip", dest="zip_code", default=None, help="Limit to one ZIP code")
    parser.add_argument("--zips", dest="zip_codes", default=None, help="Comma-separated Shelby ZIP codes (default: all)")
    parser.add_argument("--bergen-municipalities", dest="bergen_municipalities", default=None, help="Comma-separated Bergen County municipality names (default: all 39)")
    parser.add_argument("--min-price", dest="min_price", type=float, default=50_000.0, help="Minimum sale price filter in USD (default: 50000)")
    parser.add_argument("--no-email", action="store_true", help="Skip email delivery")
    parser.add_argument("--no-sheets", action="store_true", help="Skip Google Sheets upload")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("Cash Buyer Scraper — %s (days=%d)", date.today().isoformat(), args.days)
    logger.info("=" * 60)

    all_cash_records: list[dict] = []

    # ── 1. Shelby County (Memphis TN) ────────────────────────────────────────
    logger.info("--- Shelby County (Memphis TN) ---")
    try:
        from src.scrapers.shelby import scrape_shelby, build_deed_url
        from src.cash_filter import filter_cash_sales_shelby

        zip_list = [z.strip() for z in args.zip_codes.split(",") if z.strip()] if args.zip_codes else None
        raw_shelby = scrape_shelby(days=args.days, zip_codes=zip_list, zip_code=args.zip_code)
        logger.info("Shelby: %d raw deed records", len(raw_shelby))

        cash_shelby = filter_cash_sales_shelby(raw_shelby, min_price=args.min_price)
        logger.info("Shelby: %d confirmed cash sales after filter", len(cash_shelby))

        for r in cash_shelby:
            r["deed_url"] = build_deed_url(r.get("record_number", ""), r.get("sale_date", ""))

        # ── Assessor lookup pass (Shelby only — no Bergen County equivalent) ──
        if cash_shelby:
            logger.info("--- Assessor lookups (%d confirmed Shelby cash sales) ---", len(cash_shelby))
            try:
                from src.assessor import lookup_parcels
                from src.normalize import names_share_tokens

                addresses = [r.get("property_address", "") for r in cash_shelby]
                assessor_results = lookup_parcels(addresses)

                for r in cash_shelby:
                    info = assessor_results.get(r.get("property_address", ""), {})
                    r["assessor_url"] = info.get("assessor_url", "")
                    r["assessor_owner_name"] = info.get("owner_name", "")
                    r["assessor_match_type"] = info.get("match_type", "none")
                    r["assessor_sales_history"] = info.get("sales_history", [])
                    r["assessor_candidates"] = info.get("candidates", [])
                    # Only trust the Assessor's mailing address once its owner
                    # has already updated to reflect THIS buyer (token match)
                    # -- otherwise it's the prior owner/seller's mailing
                    # address, which would silently inject wrong data given
                    # the ~60% week-1-2 lag rate measured against live data.
                    owner_name = info.get("owner_name", "")
                    if owner_name and names_share_tokens(owner_name, r.get("buyer_name", "")):
                        r["buyer_mailing_address"] = info.get("owner_mailing_address", "")
            except Exception as e:
                logger.error("Assessor lookup pass failed: %s", e, exc_info=True)
                for r in cash_shelby:
                    r.setdefault("assessor_url", "")
                    r.setdefault("assessor_match_type", "none")

        all_cash_records.extend(cash_shelby)
    except Exception as e:
        logger.error("Shelby County scraper failed: %s", e, exc_info=True)

    # ── 2. Bergen County (Bergen County NJ) ──────────────────────────────────
    # Uses a single Playwright browser session for both scraping and cash-filter
    # mortgage checks (reCAPTCHA v3 token is shared across both steps).
    logger.info("--- Bergen County NJ ---")
    try:
        from src.scrapers.bergen import run_bergen_pipeline

        bergen_muni_list = [m.strip() for m in args.bergen_municipalities.split(",") if m.strip()] if args.bergen_municipalities else None
        cash_bergen = run_bergen_pipeline(days=args.days, zip_code=args.zip_code, municipalities=bergen_muni_list)
        logger.info("Bergen: %d confirmed cash sales after filter", len(cash_bergen))
        all_cash_records.extend(cash_bergen)
    except Exception as e:
        logger.error("Bergen County scraper failed: %s", e, exc_info=True)

    if not all_cash_records:
        logger.warning("No cash records found — nothing to output")
        return

    # ── 3. Buyer history ─────────────────────────────────────────────────────
    logger.info("--- Updating buyer history ---")
    try:
        from src.buyer_history import init_db, insert_records, enrich_with_history
        init_db()
        # Insert BEFORE enriching so this run's records count toward 90-day totals
        insert_records(all_cash_records)
        all_cash_records = enrich_with_history(all_cash_records)
    except Exception as e:
        logger.error("Buyer history update failed: %s", e, exc_info=True)
        for r in all_cash_records:
            r.setdefault("times_bought_90d", 0)

    # ── 4. Priority scoring ──────────────────────────────────────────────────
    logger.info("--- Scoring priority ---")
    try:
        from src.priority import enrich_with_priority
        all_cash_records = enrich_with_priority(all_cash_records)
        hot   = sum(1 for r in all_cash_records if r.get("priority") == "Hot")
        warm  = sum(1 for r in all_cash_records if r.get("priority") == "Warm")
        std   = sum(1 for r in all_cash_records if r.get("priority") == "Standard")
        logger.info("Priority breakdown — Hot: %d  Warm: %d  Standard: %d", hot, warm, std)
    except Exception as e:
        logger.error("Priority scoring failed: %s", e, exc_info=True)

    # ── 4b. Remarks generation ───────────────────────────────────────────────
    logger.info("--- Generating remarks ---")
    try:
        from src.buyer_history import get_purchase_history_for_address
        from src.remarks import generate_remarks

        for r in all_cash_records:
            history_rows = get_purchase_history_for_address(r.get("property_address", ""))
            r["remarks"] = generate_remarks(r, history_rows)
    except Exception as e:
        logger.error("Remarks generation failed: %s", e, exc_info=True)
        for r in all_cash_records:
            r.setdefault("remarks", "")

    # ── 5. CSV output ────────────────────────────────────────────────────────
    csv_path = ""
    logger.info("--- Writing CSV ---")
    try:
        from src.output import write_csv, write_excel
        csv_path = write_csv(all_cash_records)
        xlsx_path = write_excel(all_cash_records)
        logger.info("Output: %s", csv_path)
        logger.info("Output: %s", xlsx_path)
    except Exception as e:
        logger.error("Output write failed: %s", e, exc_info=True)

    # ── 6. Google Sheets ─────────────────────────────────────────────────────
    if not args.no_sheets:
        logger.info("--- Uploading to Google Sheets ---")
        try:
            from src.output import append_to_google_sheet
            append_to_google_sheet(all_cash_records)
        except Exception as e:
            logger.error("Google Sheets upload failed: %s", e, exc_info=True)
    else:
        logger.info("Google Sheets upload skipped (--no-sheets)")

    # ── 7. Email delivery ────────────────────────────────────────────────────
    if not args.no_email and csv_path:
        logger.info("--- Sending email report ---")
        try:
            from src.email_sender import send_report
            send_report(csv_path, record_count=len(all_cash_records))
        except Exception as e:
            logger.error("Email delivery failed: %s", e, exc_info=True)
    elif args.no_email:
        logger.info("Email delivery skipped (--no-email)")

    logger.info("=" * 60)
    logger.info(
        "Done — %d total cash records | Hot: %d | CSV: %s",
        len(all_cash_records),
        sum(1 for r in all_cash_records if r.get("priority") == "Hot"),
        csv_path or "(none)",
    )
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
