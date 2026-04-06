import logging
import re
from datetime import date
from urllib.parse import urlencode
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

logger = logging.getLogger(__name__)

BASE_URL = "https://www.memphisdailynews.com"
RECORDS_URL = f"{BASE_URL}/PublicRecords.aspx"


def scrape_probate(page: Page, target_date: date | None = None) -> list[dict]:
    """
    Scrape all Probate Court filings listed for the given date (defaults to
    today). Unlike Tax Liens, the only thing we need from Memphis Daily News
    here is each filing's docket number — the deceased's name and the
    petitioner/executor name come from the Shelby County docket report
    (see src/docket_lookup.py), which is more complete than the MDN listing.
    """
    if target_date is None:
        target_date = date.today()

    date_str = f"{target_date.month}/{target_date.day}/{target_date.year}"
    url = RECORDS_URL + "?" + urlencode({
        "recordsDate": date_str,
        "grp": "Probate Court",
        "cty": "Shelby",
    })

    logger.info("  SOURCE (MDN probate) --> %s", url)
    page.goto(url, wait_until="domcontentloaded")

    try:
        page.wait_for_selector('a[href*="OpenChild"]', state="attached", timeout=30000)
    except PlaywrightTimeoutError:
        logger.info("No Probate records found for %s (page loaded but no listing rows)", target_date)
        return []

    records = _extract_all_pages(page, target_date)
    logger.info("Extracted %d probate records for %s", len(records), target_date)
    return records


def _extract_all_pages(page: Page, target_date: date) -> list[dict]:
    """Collect docket numbers across all pages, following Next pagination if present."""
    records = []
    page_num = 1

    while True:
        logger.info("Scraping listing page %d", page_num)
        docket_numbers = _get_docket_numbers(page)
        logger.info("Found %d records on page %d", len(docket_numbers), page_num)

        for docket_number in docket_numbers:
            records.append({
                "filing_date": target_date.isoformat(),
                "record_type": "Probate",
                "docket_number": docket_number,
            })

        next_link = page.query_selector('a:has-text("Next"), a.next-page, a[rel="next"]')
        if not next_link:
            break

        logger.info("Following pagination to page %d", page_num + 1)
        next_link.click()
        page.wait_for_load_state("domcontentloaded")
        page_num += 1

    return records


def _get_docket_numbers(page: Page) -> list[str]:
    """
    From the listing table, extract the docket number for every row.

    Table row structure:
      <td><a href="javascript:OpenChild('PR035936','623')">View</a></td>
      <td>06/10/26</td>
      <td>Brown, Toof; Suzanne C Brown</td>
      <td>&nbsp;</td>

    The first argument to OpenChild is the probate docket number itself
    (e.g. "PR035936"), unlike Tax Liens where it's a numeric internal ID.
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
        match = re.search(r"OpenChild\('([^']+)','\d+'\)", href)
        if not match:
            continue

        results.append(match.group(1))

    return results
