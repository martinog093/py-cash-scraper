import logging
import sys
from datetime import date, datetime, timedelta
from playwright.sync_api import sync_playwright

from src.auth import login, logout
from src.scrapers.tax_lien import scrape_tax_liens
from src.assessor import verify_ownership
from src.output import write_output
from src.logging_setup import setup_logging

OUTPUT_FILE = "output/tax_lien.xlsx"


def run(start_date: date | None = None, end_date: date | None = None):
    setup_logging()
    logger = logging.getLogger(__name__)
    discard_logger = logging.getLogger("discarded")

    start_time = datetime.now()

    if start_date is None:
        start_date = date.today()
    if end_date is None:
        end_date = start_date

    if end_date < start_date:
        logger.error("End date %s is before start date %s", end_date, start_date)
        return

    date_range = [start_date + timedelta(days=i) for i in range((end_date - start_date).days + 1)]

    logger.info(
        "=== Memphis Scraper M1 — Tax Liens — started at %s — %s to %s (%d day(s)) ===",
        start_time, start_date, end_date, len(date_range),
    )

    all_records = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )

        # --- Step 1: Login ---
        try:
            page = login(context)
        except RuntimeError as e:
            logger.error(str(e))
            browser.close()
            return

        try:
            for target_date in date_range:
                logger.info("=== Scraping %s ===", target_date)

                # --- Step 2: Scrape Tax Liens ---
                raw_records = scrape_tax_liens(page, target_date)
                logger.info("Scraped %d raw tax lien records for %s", len(raw_records), target_date)

                # --- Step 3: Verify each record against Assessor portal ---
                for i, record in enumerate(raw_records, 1):
                    name = record.get("primary_name", "").strip()
                    address = record.get("address", "").strip()

                    logger.info("--- %s — Record %d/%d ---", target_date, i, len(raw_records))
                    logger.info("  Name:    %s", name or "(missing)")
                    logger.info("  Address: %s", address or "(none)")
                    logger.info("  Docket:  %s", record.get("docket_number") or "(none)")
                    logger.info("  Amount:  %s", record.get("debt_amount") or "(none)")

                    if not name:
                        record["status"] = "Discarded"
                        record["discard_reason"] = "Missing owner name"
                        discard_logger.info(
                            "[Tax Lien] DISCARDED | name='' | reason=Missing owner name"
                        )
                        logger.info("  Result:  DISCARDED — missing owner name")
                        all_records.append(record)
                        continue

                    result = verify_ownership(name, address)

                    if result is None:
                        record["status"] = "Discarded"
                        record["discard_reason"] = "No property found under name"
                        record["verified_address"] = ""
                        record["unverified_address"] = ""
                        record["parcel_id"] = ""
                        discard_logger.info(
                            "[Tax Lien] DISCARDED | name=%s | reason=No property found under name",
                            name,
                        )
                        logger.info("  Result:  DISCARDED — no property match on Assessor")
                    elif result.get("status") == "Assessor Unavailable":
                        record["status"] = "Assessor Unavailable"
                        record["discard_reason"] = "Assessor portal unreachable"
                        record["verified_address"] = ""
                        record["unverified_address"] = ""
                        record["parcel_id"] = ""
                        logger.warning("  Result:  ASSESSOR UNAVAILABLE for name=%s", name)
                    elif result["status"] == "Needs Review":
                        record["status"] = "Needs Review"
                        record["discard_reason"] = "Tax lien address did not match Assessor record(s) for this name"
                        record["verified_address"] = result.get("verified_address", "")
                        record["unverified_address"] = result.get("unverified_address", "")
                        record["parcel_id"] = result.get("parcel_id", "")
                        logger.info(
                            "  Result:  NEEDS REVIEW — candidates=%s  unverified_address=%s  parcels=%s",
                            record["verified_address"],
                            record["unverified_address"],
                            record["parcel_id"],
                        )
                    else:
                        record["status"] = "Verified"
                        record["discard_reason"] = ""
                        record["verified_address"] = result.get("verified_address", "")
                        record["unverified_address"] = result.get("unverified_address", "")
                        record["parcel_id"] = result.get("parcel_id", "")
                        logger.info(
                            "  Result:  VERIFIED — verified_address=%s  unverified_address=%s  parcel=%s",
                            record["verified_address"],
                            record["unverified_address"],
                            record["parcel_id"],
                        )

                    all_records.append(record)
        finally:
            # Always log out so the server-side session ends now, instead of
            # staying "active" until it times out and blocking the next run's login.
            logout(page)
            browser.close()

    # --- Step 4: Write output ---
    write_output(all_records, OUTPUT_FILE)

    verified = sum(1 for r in all_records if r.get("status") == "Verified")
    needs_review = sum(1 for r in all_records if r.get("status") in ("Needs Review", "Assessor Unavailable"))
    discarded = sum(1 for r in all_records if r.get("status") == "Discarded")
    duration = (datetime.now() - start_time).seconds

    logger.info(
        "=== Run complete — %d verified, %d needs review, %d discarded, %ds elapsed ===",
        verified, needs_review, discarded, duration,
    )


if __name__ == "__main__":
    # Usage:
    #   python tax_lien_scraper.py                              -> today only
    #   python tax_lien_scraper.py YYYY-MM-DD                   -> a single date
    #   python tax_lien_scraper.py YYYY-MM-DD YYYY-MM-DD        -> a date range (inclusive)
    if len(sys.argv) >= 3:
        start = date.fromisoformat(sys.argv[1])
        end = date.fromisoformat(sys.argv[2])
    elif len(sys.argv) == 2:
        start = date.fromisoformat(sys.argv[1])
        end = start
    else:
        start = None
        end = None

    run(start, end)
