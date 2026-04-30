"""
Shelby County Register of Deeds scraper.

Uses the legacy p2.php POST endpoint — no login, no JavaScript, no Playwright needed.
Returns raw deed records; cash filtering is done separately in cash_filter.py.

Endpoint: POST https://search.register.shelby.tn.us/p2.php
Results:  HTML table with id="hit_list"
Max:      1,000 results per search (unlikely to hit for 7-day/zip searches)
"""

import logging
import time
from datetime import date, datetime, timedelta

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE_URL = "https://search.register.shelby.tn.us"
SEARCH_URL = f"{BASE_URL}/p2.php"

SHELBY_ZIPS = [
    "38002", "38016", "38017", "38018", "38028", "38053", "38054",
    "38101", "38103", "38104", "38105", "38106", "38107", "38108",
    "38109", "38111", "38112", "38113", "38114", "38115", "38116",
    "38117", "38118", "38119", "38120", "38122", "38125", "38126",
    "38127", "38128", "38130", "38131", "38132", "38133", "38134",
    "38135", "38136", "38137", "38138", "38139", "38141",
]

# Crawl-delay per robots.txt
CRAWL_DELAY_SECONDS = 31

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": BASE_URL,
    "Origin": BASE_URL,
}


def scrape_shelby(
    days: int = 7,
    zip_codes: list[str] | None = None,
    zip_code: str | None = None,
) -> list[dict]:
    """
    Scrape Warranty Deed and Quit Claim filings from Shelby County.

    Args:
        days:      How many days back to search (default 7 for weekly run).
        zip_codes: If provided, only scrape these ZIPs instead of the full list.
        zip_code:  Legacy single-ZIP parameter; ignored if zip_codes is set.

    Returns list of raw deed record dicts (not yet cash-filtered).
    """
    end_date = date.today()
    start_date = end_date - timedelta(days=days)

    if zip_codes:
        zips_to_run = zip_codes
    elif zip_code:
        zips_to_run = [zip_code]
    else:
        zips_to_run = SHELBY_ZIPS
    logger.info(
        "Shelby County: scraping %d ZIP(s) from %s to %s",
        len(zips_to_run), start_date, end_date,
    )

    all_records: list[dict] = []

    with httpx.Client(headers=HEADERS, timeout=60, follow_redirects=True) as client:
        for i, zipcode in enumerate(zips_to_run):
            logger.info("  ZIP %s (%d/%d)", zipcode, i + 1, len(zips_to_run))
            try:
                records = _search_zip(client, zipcode, start_date, end_date)
                logger.info("    -> %d records", len(records))
                all_records.extend(records)
            except Exception as e:
                logger.error("    ZIP %s failed: %s", zipcode, e)

            # Respect the 30-second crawl delay between ZIP requests
            if i < len(zips_to_run) - 1:
                time.sleep(CRAWL_DELAY_SECONDS)

    # Deduplicate by record_number — the same deed can appear in multiple
    # ZIP searches when a property straddles ZIP boundaries.
    seen: set[str] = set()
    unique: list[dict] = []
    for r in all_records:
        key = r.get("record_number", "")
        if key and key not in seen:
            seen.add(key)
            unique.append(r)
        elif not key:
            unique.append(r)

    if len(unique) < len(all_records):
        logger.info(
            "Shelby County: %d total raw records (%d duplicates removed)",
            len(unique), len(all_records) - len(unique),
        )
    else:
        logger.info("Shelby County: %d total raw records", len(all_records))
    return unique


def _search_zip(
    client: httpx.Client,
    zipcode: str,
    start_date: date,
    end_date: date,
) -> list[dict]:
    """POST one ZIP-code search and parse the HTML hit list."""
    payload = {
        "propzip":    zipcode,
        "startDate":  start_date.strftime("%m/%d/%Y"),
        "endDate":    end_date.strftime("%m/%d/%Y"),
        "itype2":     "WD",   # Warranty Deed
        "itype3":     "QC",   # Quit Claim
        "searchtype": "ADDR",
    }

    resp = client.post(SEARCH_URL, data=payload)
    resp.raise_for_status()

    return _parse_hit_list(resp.text, zipcode)


def _parse_hit_list(html: str, zipcode: str) -> list[dict]:
    """
    Parse the p2.php HTML response.

    The results are in <table id="hit_list"> with columns:
      Inst # | Inst Code | Grantor/Debtor | Grantee/Secured Party |
      Date | Transfer Amount | Mortgage Amount | Street Number |
      Street Name | City | State | Zip | Image
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id="hit_list")

    if not table:
        # No results or error page
        if "No records found" in html or "0 records" in html.lower():
            return []
        logger.debug("No hit_list table found for ZIP %s", zipcode)
        return []

    rows = table.find_all("tr")
    if len(rows) <= 1:
        return []

    records = []
    for row in rows[1:]:  # skip header row
        cells = row.find_all("td")
        # Columns: Details(0) | Inst#(1) | Code(2) | Grantor(3) | Grantee(4) |
        #          Date(5) | Transfer(6) | Mortgage(7) | StrNum(8) | StrName(9) |
        #          City(10) | State(11) | Zip(12) | Image(13)
        if len(cells) < 13:
            continue

        inst_num    = cells[1].get_text(strip=True)
        inst_code   = cells[2].get_text(strip=True)
        # Joint grantors/grantees are rendered as separate lines within the
        # same cell (e.g. two names via <br>). get_text(separator=" ") would
        # smash them into one run-on string ("ATKINSON ROBYN ATKINSON VINCENT
        # B"), so join each stripped line with "; " to keep names distinct.
        grantor     = "; ".join(cells[3].stripped_strings)
        grantee     = "; ".join(cells[4].stripped_strings)
        rec_date    = cells[5].get_text(strip=True)
        transfer    = cells[6].get_text(strip=True)
        mortgage    = cells[7].get_text(strip=True)
        street_num  = cells[8].get_text(strip=True)
        street_name = cells[9].get_text(strip=True)
        city        = cells[10].get_text(strip=True)
        state       = cells[11].get_text(strip=True)
        zip_result  = cells[12].get_text(strip=True)

        if not inst_num or inst_code not in ("WD", "QC"):
            continue

        address = " ".join(p for p in [street_num, street_name, city, state, zip_result] if p)

        records.append({
            "market":            "Memphis TN",
            "record_number":     inst_num,
            "deed_type":         "Warranty Deed" if inst_code == "WD" else "Quit Claim",
            "seller_name":       grantor,
            "buyer_name":        grantee,
            "sale_date":         rec_date,
            "purchase_price":    _parse_amount(transfer),
            "mortgage_amount":   _parse_amount(mortgage),
            "property_address":  address,
            "buyer_mailing_address": "",  # not in p2 results; available in pdetail
            "search_zip":        zipcode,
        })

    return records


def _parse_amount(text: str) -> float:
    """Convert '$279,900.00' or '279900.00' to float. Returns 0.0 on failure."""
    try:
        return float(text.replace("$", "").replace(",", "").strip() or "0")
    except ValueError:
        return 0.0


def build_deed_url(record_number: str, sale_date: str) -> str:
    """
    Construct the Shelby County Register deed-detail URL. Confirmed via a
    plain GET (no session/cookie needed):
      pdetail.php?year={YYYY}&instnum={record_number}&db=0&book=**0

    `year` is derived from sale_date (MM/DD/YYYY), not parsed out of
    record_number, even though they happen to share a leading "26"/"2026" --
    sale_date is the authoritative date source. `book=**0` is a literal
    fixed string the site itself uses in its own "Details" links, not a
    placeholder to fill with a real book number -- pass it through unchanged.
    """
    try:
        year = datetime.strptime(sale_date, "%m/%d/%Y").year
    except ValueError:
        return ""
    return f"{BASE_URL}/pdetail.php?year={year}&instnum={record_number}&db=0&book=**0"
