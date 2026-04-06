import logging
import sys
from datetime import date, datetime, timedelta
from playwright.sync_api import sync_playwright

from src.auth import login, logout
from src.scrapers.divorce import scrape_divorce
from src.assessor import search_by_name
from src.output import write_output
from src.logging_setup import setup_logging

OUTPUT_FILE = "output/divorce.xlsx"


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
        "=== Memphis Scraper — Divorce — started at %s — %s to %s (%d day(s)) ===",
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

        try:
            page = login(context)
        except RuntimeError as e:
            logger.error(str(e))
            browser.close()
            return

        try:
            for target_date in date_range:
                logger.info("=== Scraping %s ===", target_date)

                raw_records = scrape_divorce(page, target_date)
                logger.info(
                    "Found %d divorce filing(s) for %s", len(raw_records), target_date
                )

                for i, record in enumerate(raw_records, 1):
                    plaintiff = record.get("primary_name", "").strip()
                    defendant = record.get("secondary_name", "").strip()

                    logger.info("--- %s — Record %d/%d ---", target_date, i, len(raw_records))
                    logger.info("  Plaintiff: %s", plaintiff or "(missing)")
                    logger.info("  Defendant: %s", defendant or "(missing)")
                    logger.info("  Docket:    %s", record.get("docket_number", ""))

                    if not plaintiff and not defendant:
                        record["status"] = "Discarded"
                        record["discard_reason"] = "Missing both plaintiff and defendant names"
                        record["verified_address"] = ""
                        record["parcel_id"] = ""
                        discard_logger.info(
                            "[Divorce] DISCARDED | docket=%s | reason=Missing both plaintiff and defendant names",
                            record.get("docket_number", ""),
                        )
                        all_records.append(record)
                        continue

                    # Try plaintiff first; fall back to defendant if no property found
                    result = None
                    searched_name = None

                    if plaintiff:
                        result = search_by_name(plaintiff)
                        searched_name = plaintiff

                    if result is None and defendant:
                        result = search_by_name(defendant)
                        searched_name = defendant

                    if result is None:
                        record["status"] = "Discarded"
                        record["discard_reason"] = "No property found under plaintiff or defendant name"
                        record["verified_address"] = ""
                        record["parcel_id"] = ""
                        discard_logger.info(
                            "[Divorce] DISCARDED | plaintiff=%s | defendant=%s | reason=No property found under plaintiff or defendant name",
                            plaintiff, defendant,
                        )
                        logger.info("  Result:  DISCARDED — no property match for either party")
                    elif result.get("status") == "Assessor Unavailable":
                        record["status"] = "Assessor Unavailable"
                        record["discard_reason"] = "Assessor portal unreachable"
                        record["verified_address"] = ""
                        record["parcel_id"] = ""
                        logger.warning(
                            "  Result:  ASSESSOR UNAVAILABLE for name=%s", searched_name
                        )
                    elif result["status"] == "Needs Review":
                        record["status"] = "Needs Review"
                        record["discard_reason"] = (
                            f"Multiple properties found under '{searched_name}' — review candidates"
                        )
                        record["verified_address"] = result.get("verified_address", "")
                        record["parcel_id"] = result.get("parcel_id", "")
                        logger.info(
                            "  Result:  NEEDS REVIEW — name=%s  candidates=%s  parcels=%s",
                            searched_name, record["verified_address"], record["parcel_id"],
                        )
                    else:
                        record["status"] = "Verified"
                        record["discard_reason"] = ""
                        record["verified_address"] = result.get("verified_address", "")
                        record["parcel_id"] = result.get("parcel_id", "")
                        logger.info(
                            "  Result:  VERIFIED — name=%s  verified_address=%s  parcel=%s",
                            searched_name, record["verified_address"], record["parcel_id"],
                        )

                    all_records.append(record)
        finally:
            logout(page)
            browser.close()

    write_output(all_records, OUTPUT_FILE)

    verified = sum(1 for r in all_records if r.get("status") == "Verified")
    needs_review = sum(
        1 for r in all_records
        if r.get("status") in ("Needs Review", "Assessor Unavailable")
    )
    discarded = sum(1 for r in all_records if r.get("status") == "Discarded")
    duration = (datetime.now() - start_time).seconds

    logger.info(
        "=== Run complete — %d verified, %d needs review, %d discarded, %ds elapsed ===",
        verified, needs_review, discarded, duration,
    )


if __name__ == "__main__":
    # Usage:
    #   python divorce_scraper.py                              -> today only
    #   python divorce_scraper.py YYYY-MM-DD                   -> a single date
    #   python divorce_scraper.py YYYY-MM-DD YYYY-MM-DD        -> a date range (inclusive)
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
