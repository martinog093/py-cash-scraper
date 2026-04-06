"""
Integration test — runs the full parsing → assessor → output pipeline
against the client-supplied sample HTML files (no MDN login required).

What this covers:
  ✓ Listing-page parsing  (_get_view_links)
  ✓ Detail-page parsing   (_parse_detail_fields / _scrape_detail_page logic)
  ✓ Live Assessor lookup  (verify_ownership — hits assessormelvinburgess.com)
  ✓ Excel output          (write_output)

What is NOT covered (requires real credentials):
  ✗ MDN login
  ✗ Live MDN page navigation

Run:
    python test_integration.py
Output:
    output/test_integration.xlsx
    logs/run.log
    logs/discarded.log
"""

import logging
import os
import sys
from datetime import date
from pathlib import Path
from playwright.sync_api import sync_playwright

# ── Logging setup ──────────────────────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)

fmt = "%(asctime)s [%(levelname)s] %(name)s — %(message)s"
logging.basicConfig(
    level=logging.INFO,
    format=fmt,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/run.log"),
    ],
)

discard_logger = logging.getLogger("discarded")
discard_logger.setLevel(logging.INFO)
discard_logger.propagate = False
_dh = logging.FileHandler("logs/discarded.log")
_dh.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
discard_logger.addHandler(_dh)

logger = logging.getLogger(__name__)

# ── Imports ────────────────────────────────────────────────────────────────────
from src.scrapers.tax_lien import _get_view_links, _parse_detail_fields
from src.assessor import verify_ownership
from src.output import write_output

OUTPUT_FILE = "output/test_integration.xlsx"

# Absolute paths to the client-supplied HTML files
LISTING_HTML  = Path("Client_sent_html/taxlienpage.html").resolve()
DETAIL_HTML   = Path("Client_sent_html/taxlienRecord Details.html").resolve()

# ── Main ───────────────────────────────────────────────────────────────────────

def run():
    logger.info("=== Integration test started ===")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"
        )

        # ── Step 1: Parse the listing page ────────────────────────────────────
        logger.info("Loading listing page: %s", LISTING_HTML)
        page.goto(f"file://{LISTING_HTML}")
        view_links = _get_view_links(page)
        logger.info("Listing parser found %d records", len(view_links))
        for fk, xid, name in view_links[:5]:
            logger.info("  fk=%-10s  xid=%s  name=%s", fk, xid, name)
        if len(view_links) > 5:
            logger.info("  ... and %d more", len(view_links) - 5)

        # ── Step 2: Parse the detail page ─────────────────────────────────────
        logger.info("Loading detail page: %s", DETAIL_HTML)
        page.goto(f"file://{DETAIL_HTML}")
        fields = _parse_detail_fields(page)
        logger.info("Detail parser extracted: %s", fields)

        browser.close()

    # Build one test record from the parsed detail fields
    # (In the real pipeline this loops over all records; here we use the one
    #  sample detail page we have.)
    primary_name = fields.get("debtor", "").strip()
    address      = fields.get("address", "").strip()
    instrument   = fields.get("instrument_number", "unknown")
    filing_date  = fields.get("recording_date") or date.today().isoformat()

    logger.info("--- Verifying record ---")
    logger.info("  Name:    %s", primary_name)
    logger.info("  Address: %s", address)
    logger.info("  Docket:  %s", instrument)

    # ── Step 3: Assessor verification ─────────────────────────────────────────
    result = verify_ownership(primary_name, address)

    record = {
        "filing_date":    filing_date,
        "record_type":    "Tax Lien",
        "primary_name":   primary_name,
        "secondary_name": "",
        "docket_number":  instrument,
        "debt_amount":    fields.get("debt_amount", ""),
    }

    if result is None:
        record["verified_address"]   = ""
        record["unverified_address"] = ""
        record["parcel_id"]          = ""
        record["status"]             = "Discarded"
        record["discard_reason"]     = "No property found under name"
        discard_logger.info(
            "[Tax Lien] DISCARDED | name=%s | reason=No property found under name",
            primary_name,
        )
        logger.info("  Result: DISCARDED — no property match on Assessor")
    elif result.get("status") == "Assessor Unavailable":
        record["verified_address"]   = ""
        record["unverified_address"] = ""
        record["parcel_id"]          = ""
        record["status"]             = "Assessor Unavailable"
        record["discard_reason"]     = "Assessor portal unreachable"
        logger.warning("  Result: ASSESSOR UNAVAILABLE")
    elif result["status"] == "Needs Review":
        record["verified_address"]   = result["verified_address"]
        record["unverified_address"] = result["unverified_address"]
        record["parcel_id"]          = result["parcel_id"]
        record["status"]             = "Needs Review"
        record["discard_reason"]     = "Tax lien address did not match Assessor record(s) for this name"
        logger.info(
            "  Result: NEEDS REVIEW — candidates=%s  unverified_address=%s  parcels=%s",
            result["verified_address"],
            result["unverified_address"],
            result["parcel_id"],
        )
    else:
        record["verified_address"]   = result["verified_address"]
        record["unverified_address"] = result["unverified_address"]
        record["parcel_id"]          = result["parcel_id"]
        record["status"]             = "Verified"
        record["discard_reason"]     = ""
        logger.info(
            "  Result: VERIFIED — verified_address=%s  unverified_address=%s  parcel=%s",
            result["verified_address"],
            result["unverified_address"],
            result["parcel_id"],
        )

    # ── Step 4: Write output ───────────────────────────────────────────────────
    write_output([record], OUTPUT_FILE)
    logger.info("=== Integration test complete — open %s ===", OUTPUT_FILE)


if __name__ == "__main__":
    run()
