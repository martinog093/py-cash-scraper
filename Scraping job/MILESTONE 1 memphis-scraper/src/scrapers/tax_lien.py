import logging
import re
from datetime import date
from urllib.parse import urlencode
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

logger = logging.getLogger(__name__)

BASE_URL = "https://www.memphisdailynews.com"
RECORDS_URL = f"{BASE_URL}/PublicRecords.aspx"
DETAIL_URL = f"{BASE_URL}/Search/Details/Details.aspx"


def scrape_tax_liens(page: Page, target_date: date | None = None) -> list[dict]:
    """Scrape all Tax Lien & Release records for the given date (defaults to today)."""
    if target_date is None:
        target_date = date.today()

    # Navigate directly to the Tax Liens & Releases category for the given date
    date_str = f"{target_date.month}/{target_date.day}/{target_date.year}"
    url = RECORDS_URL + "?" + urlencode({
        "recordsDate": date_str,
        "grp": "Tax Liens & Releases",
        "cty": "Shelby",
    })

    logger.info("  SOURCE (MDN tax liens) --> %s", url)
    page.goto(url, wait_until="domcontentloaded")

    # Wait for actual listing rows — more specific than table.data-table (which
    # matches 3+ summary tables on the page) and handles empty-date pages cleanly.
    try:
        page.wait_for_selector('a[href*="OpenChild"]', state="attached", timeout=30000)
    except PlaywrightTimeoutError:
        logger.info("No Tax Lien records found for %s (page loaded but no listing rows)", target_date)
        return []

    records = _extract_all_pages(page, target_date)
    logger.info("Extracted %d tax lien records for %s", len(records), target_date)
    return records


def _extract_all_pages(page: Page, target_date: date) -> list[dict]:
    """Collect records across all pages, following Next pagination if present."""
    records = []
    page_num = 1

    while True:
        logger.info("Scraping listing page %d", page_num)
        view_links = _get_view_links(page)
        logger.info("Found %d records on page %d", len(view_links), page_num)

        for i, (fk, xid, listing_name) in enumerate(view_links):
            if i > 0:
                page.wait_for_timeout(2000)
            record = _scrape_detail_page(page, fk, xid, listing_name, target_date)
            if record:
                records.append(record)
            # Return to the listing page after each detail visit
            page.go_back(wait_until="domcontentloaded")

        # Check for a Next page link
        next_link = page.query_selector('a:has-text("Next"), a.next-page, a[rel="next"]')
        if not next_link:
            break

        logger.info("Following pagination to page %d", page_num + 1)
        next_link.click()
        page.wait_for_load_state("domcontentloaded")
        page_num += 1

    return records


def _get_view_links(page: Page) -> list[tuple[str, str, str]]:
    """
    From the listing table, extract (fk, xid, name) for every View link.

    Table row structure:
      <td><a href="javascript:OpenChild('26044818','604')">View</a></td>
      <td>06/01/26</td>
      <td>Frazetta Ventures Llc; Tennessee Department Of Labor...</td>
      <td>&nbsp;</td>   ← address always blank here
    """
    rows = page.query_selector_all("table.data-table tbody tr, table.data-table tr:not(:first-child)")
    results = []

    for row in rows:
        cells = row.query_selector_all("td")
        if len(cells) < 3:
            continue

        view_cell = cells[0]
        link = view_cell.query_selector("a[href*='OpenChild']")
        if not link:
            continue

        href = link.get_attribute("href") or ""
        match = re.search(r"OpenChild\('(\d+)','(\d+)'\)", href)
        if not match:
            continue

        fk, xid = match.group(1), match.group(2)
        listing_name = cells[2].inner_text().strip()
        results.append((fk, xid, listing_name))

    return results


# Maps the detail page's <b>Label</b> text (lowercased) to our internal field keys
_DETAIL_FIELD_MAP = {
    "instrument #":      "instrument_number",
    "recording date":    "recording_date",
}

# Grantor/Debtor N rows → the debtor on a new lien, the creditor on a release
_GRANTOR_RE = re.compile(r'^grantor/debtor\s*\d*$')
# Grantee/Securited N rows → the creditor on a new lien, the debtor on a release
_GRANTEE_RE = re.compile(r'^grantee/securited\s*\d*$')

_AMOUNT_RE = re.compile(r'\$\s*[\d,]+(?:\.\d{2})?')


def _parse_detail_fields(page: Page) -> dict:
    """
    Parse the label/value rows inside the Record Details table.

    Row layout: <td><b>Label</b></td><td>Value</td><td>...</td>
    The property address spans two rows: a labeled "Address" row holding
    the street, immediately followed by an unlabeled row holding the
    city/state/zip — both must be combined into one address string.

    For new liens  (e.g. "State Tax Lien"):         Grantor/Debtor = property owner
    For releases   (e.g. "State Tax Lien Release"):  Grantee        = property owner
    We capture both and let _scrape_detail_page decide which to use based on
    the document type stored in fields["document_type"].
    """
    fields: dict[str, str] = {}
    address_parts: list[str] = []
    debtors: list[str] = []
    grantees: list[str] = []
    expect_address_continuation = False

    # Document type is in the <h2> inside .service-header
    doc_type_el = page.query_selector("#record-details .service-header h2")
    if doc_type_el:
        fields["document_type"] = doc_type_el.inner_text().strip()

    rows = page.query_selector_all("#record-details table.data-table tr")
    for row in rows:
        cells = row.query_selector_all("td")
        if len(cells) < 2:
            continue

        label = cells[0].inner_text().strip()
        value = cells[1].inner_text().strip()

        if label:
            expect_address_continuation = label.lower() == "address"
            if expect_address_continuation:
                address_parts = [value]
                continue

            label_lower = label.lower()
            if _GRANTOR_RE.match(label_lower):
                debtors.append(value)
                continue
            if _GRANTEE_RE.match(label_lower):
                grantees.append(value)
                continue

            key = _DETAIL_FIELD_MAP.get(label_lower)
            if key == "instrument_number":
                # Cell also contains a "Click for complete legal document" link —
                # the instrument number is just the leading digits.
                number_match = re.match(r'(\d+)', value)
                fields[key] = number_match.group(1) if number_match else value
            elif key:
                fields[key] = value
            elif "debt_amount" not in fields:
                amount_match = _AMOUNT_RE.search(value)
                if amount_match:
                    fields["debt_amount"] = amount_match.group(0)
        elif expect_address_continuation:
            address_parts.append(value)
            expect_address_continuation = False

    if address_parts:
        fields["address"] = ", ".join(p for p in address_parts if p)
    if debtors:
        fields["debtor"] = "; ".join(debtors)
    if grantees:
        fields["grantee"] = "; ".join(grantees)

    return fields


def _scrape_detail_page(
    page: Page,
    fk: str,
    xid: str,
    listing_name: str,
    target_date: date,
) -> dict | None:
    """
    Navigate to the detail page for one record and extract all fields.
    Detail URL: /Search/Details/Details.aspx?fk=26044818&xid=604
    """
    detail_url = f"{DETAIL_URL}?fk={fk}&xid={xid}"
    logger.info("  Detail page --> %s", detail_url)

    page.goto(detail_url, wait_until="domcontentloaded")
    page.wait_for_selector("#record-details table.data-table", timeout=30000)

    fields = _parse_detail_fields(page)

    # For a new lien  ("State Tax Lien", "Federal Tax Lien"):
    #   Grantor/Debtor = property owner  ← use this as primary_name
    # For a release   ("State Tax Lien Release", "Federal Tax Lien Release"):
    #   Grantee        = property owner  ← use this instead
    doc_type = fields.get("document_type", "").lower()
    is_release = "release" in doc_type
    if is_release:
        primary_name = fields.get("grantee", "").strip()
        logger.info("  Release record — using Grantee as primary name")
    else:
        primary_name = fields.get("debtor", "").strip()

    # Fall back to the listing name if the detail page is missing the expected row
    if not primary_name:
        primary_name = listing_name.split(";", 1)[0].strip()

    instrument_number = fields.get("instrument_number") or fk

    record = {
        "filing_date":    fields.get("recording_date") or target_date.isoformat(),
        "record_type":    "Tax Lien Release" if is_release else "Tax Lien",
        "primary_name":   primary_name,
        "secondary_name": "",
        "docket_number":  instrument_number,
        "address":        fields.get("address", ""),
        "debt_amount":    fields.get("debt_amount", ""),
    }

    if not record["primary_name"]:
        logger.warning("Could not extract debtor name for fk=%s xid=%s", fk, xid)
        return None

    return record
