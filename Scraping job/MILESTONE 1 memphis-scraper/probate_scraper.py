import logging
import sys
from datetime import date, datetime, timedelta
from playwright.sync_api import sync_playwright

from src.auth import login, logout
from src.scrapers.probate import scrape_probate
from src.docket_lookup import get_probate_parties
from src.assessor import search_by_name
from src.output import write_output
from src.logging_setup import setup_logging

OUTPUT_FILE = "output/probate.xlsx"


def _format_party_name(name: str) -> str:
    """Convert a docket-report 'LAST, FIRST MIDDLE' (all caps) name to 'First Middle Last'."""
    name = name.strip()
    if "," not in name:
        return name.title()
    last, first = (p.strip() for p in name.split(",", 1))
    return f"{first.title()} {last.title()}"


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
        "=== Memphis Scraper M2 — Probate — started at %s — %s to %s (%d day(s)) ===",
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

                # --- Step 2: Scrape Probate Court docket numbers ---
                raw_records = scrape_probate(page, target_date)
                logger.info("Scraped %d raw probate records for %s", len(raw_records), target_date)

                # --- Step 3: Look up case parties, then verify against Assessor ---
                for i, record in enumerate(raw_records, 1):
                    docket_number = record["docket_number"]

                    logger.info("--- %s — Record %d/%d ---", target_date, i, len(raw_records))
                    logger.info("  Docket:  %s", docket_number)

                    record["unverified_address"] = "-"
                    record["debt_amount"] = ""

                    parties = get_probate_parties(docket_number)

                    if parties.get("status") == "Unavailable":
                        record["status"] = "Docket Unavailable"
                        record["discard_reason"] = "Shelby County docket report unreachable"
                        record["primary_name"] = ""
                        record["secondary_name"] = ""
                        record["verified_address"] = ""
                        record["parcel_id"] = ""
                        logger.warning("  Result:  DOCKET UNAVAILABLE for docket=%s", docket_number)
                        all_records.append(record)
                        continue

                    subject_name = parties.get("subject_name", "")
                    petitioner_names = parties.get("petitioner_names", [])
                    attorney_names = parties.get("attorney_names", [])

                    record["primary_name"] = _format_party_name(subject_name) if subject_name else ""
                    record["secondary_name"] = "; ".join(_format_party_name(n) for n in petitioner_names)
                    record["attorney_name"] = "; ".join(_format_party_name(n) for n in attorney_names)

                    logger.info("  Subject:    %s", record["primary_name"] or "(missing)")
                    logger.info("  Petitioner: %s", record["secondary_name"] or "(none)")
                    logger.info("  Attorney:   %s", record["attorney_name"] or "(none)")

                    if not subject_name:
                        record["status"] = "Discarded"
                        record["discard_reason"] = "Could not determine deceased person's name from docket report"
                        record["verified_address"] = ""
                        record["parcel_id"] = ""
                        discard_logger.info(
                            "[Probate] DISCARDED | docket=%s | reason=Could not determine deceased person's name from docket report",
                            docket_number,
                        )
                        logger.info("  Result:  DISCARDED — no subject name in docket report")
                        all_records.append(record)
                        continue

                    result = search_by_name(subject_name)

                    if result is None:
                        record["status"] = "Discarded"
                        record["discard_reason"] = "No property found under deceased name"
                        record["verified_address"] = ""
                        record["parcel_id"] = ""
                        discard_logger.info(
                            "[Probate] DISCARDED | name=%s | reason=No property found under deceased name",
                            record["primary_name"],
                        )
                        logger.info("  Result:  DISCARDED — no property match on Assessor")
                    elif result.get("status") == "Assessor Unavailable":
                        record["status"] = "Assessor Unavailable"
                        record["discard_reason"] = "Assessor portal unreachable"
                        record["verified_address"] = ""
                        record["parcel_id"] = ""
                        logger.warning("  Result:  ASSESSOR UNAVAILABLE for name=%s", record["primary_name"])
                    elif result["status"] == "Needs Review":
                        record["status"] = "Needs Review"
                        record["discard_reason"] = "Multiple properties found under deceased name — review candidates"
                        record["verified_address"] = result.get("verified_address", "")
                        record["parcel_id"] = result.get("parcel_id", "")
                        logger.info(
                            "  Result:  NEEDS REVIEW — candidates=%s  parcels=%s",
                            record["verified_address"], record["parcel_id"],
                        )
                    else:
                        record["status"] = "Verified"
                        record["discard_reason"] = ""
                        record["verified_address"] = result.get("verified_address", "")
                        record["parcel_id"] = result.get("parcel_id", "")
                        logger.info(
                            "  Result:  VERIFIED — verified_address=%s  parcel=%s",
                            record["verified_address"], record["parcel_id"],
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
    needs_review = sum(1 for r in all_records if r.get("status") in ("Needs Review", "Assessor Unavailable", "Docket Unavailable"))
    discarded = sum(1 for r in all_records if r.get("status") == "Discarded")
    duration = (datetime.now() - start_time).seconds

    logger.info(
        "=== Run complete — %d verified, %d needs review, %d discarded, %ds elapsed ===",
        verified, needs_review, discarded, duration,
    )


if __name__ == "__main__":
    # Usage:
    #   python probate_scraper.py                              -> today only
    #   python probate_scraper.py YYYY-MM-DD                   -> a single date
    #   python probate_scraper.py YYYY-MM-DD YYYY-MM-DD        -> a date range (inclusive)
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
