import logging
import re
from datetime import date, datetime
from urllib.parse import urlencode
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

logger = logging.getLogger(__name__)

BASE_URL = "https://www.memphisdailynews.com"
RECORDS_URL = f"{BASE_URL}/PublicRecords.aspx"
DETAIL_URL = f"{BASE_URL}/Search/Details/Details.aspx"

# The two MDN listing categories that contain divorce filings
_CATEGORIES = [
    ("Circuit", "634"),
    ("Chancery", "633"),
]


def scrape_divorce(page: Page, target_date: date | None = None) -> list[dict]:
    """
    Scrape divorce filings from the given date.

    Divorce filings are not a dedicated MDN category — they appear inside
    "Court Filings: Circuit" and "Court Filings: Chancery" alongside contract
    disputes, hospital liens, and other case types.  This function collects
    every candidate (fk, xid) pair from both listing pages, then opens each
    record's detail page and keeps only the ones where Type == "Divorce".
    """
    if target_date is None:
        target_date = date.today()

    # Phase 1: collect candidate (fk, xid) pairs from both listing categories
    candidates: list[tuple[str, str]] = []
    for sgrp, xid in _CATEGORIES:
        batch = _get_candidates(page, target_date, sgrp, xid)
        logger.info(
            "Found %d candidate(s) in %s Court Filings for %s", len(batch), sgrp, target_date
        )
        candidates.extend(batch)

    logger.info("Total candidates to inspect: %d", len(candidates))

    # Phase 2: open each detail page, skip non-divorce filings
    records = []
    for i, (fk, xid) in enumerate(candidates, 1):
        logger.info("Checking candidate %d/%d — fk=%s xid=%s", i, len(candidates), fk, xid)
        record = _get_divorce_detail(page, fk, xid, target_date)
        if record:
            records.append(record)

    # After visiting individual detail pages the browser is on the last detail
    # page — a minimal popup layout with no logout link.  Navigate to the
    # public records listing page (which always shows the logout link in its
    # nav) so logout() can find it when we return to the caller.
    if candidates:
        try:
            page.goto(RECORDS_URL, wait_until="domcontentloaded", timeout=30000)
        except Exception:
            pass

    logger.info("Found %d divorce filing(s) for %s", len(records), target_date)
    return records


def _get_candidates(
    page: Page, target_date: date, sgrp: str, xid: str
) -> list[tuple[str, str]]:
    """Scrape all (fk, xid) pairs from one Court Filings listing page (with pagination)."""
    date_str = f"{target_date.month}/{target_date.day}/{target_date.year}"
    url = RECORDS_URL + "?" + urlencode({
        "recordsDate": date_str,
        "grp": "Court Filings",
        "sgrp": sgrp,
        "cty": "Shelby",
    })

    logger.info("  SOURCE (MDN %s Court Filings) --> %s", sgrp, url)
    page.goto(url, wait_until="domcontentloaded")

    try:
        page.wait_for_selector('a[href*="OpenChild"]', state="attached", timeout=30000)
    except PlaywrightTimeoutError:
        logger.info("No %s Court Filings for %s", sgrp, target_date)
        return []

    return _extract_all_pages(page)


def _extract_all_pages(page: Page) -> list[tuple[str, str]]:
    """Collect (fk, xid) pairs across all listing pages, following Next pagination."""
    results = []
    page_num = 1

    while True:
        logger.info("Scraping listing page %d", page_num)
        pairs = _get_fk_xid_pairs(page)
        logger.info("Found %d row(s) on listing page %d", len(pairs), page_num)
        results.extend(pairs)

        next_link = page.query_selector('a:has-text("Next"), a.next-page, a[rel="next"]')
        if not next_link:
            break

        logger.info("Following pagination to page %d", page_num + 1)
        next_link.click()
        page.wait_for_load_state("domcontentloaded")
        page_num += 1

    return results


def _get_fk_xid_pairs(page: Page) -> list[tuple[str, str]]:
    """
    Extract (fk, xid) from every View link in the listing table.

    Row structure:
      <td><a href="javascript:OpenChild('CT-2879-26','634')">View</a></td>
      <td>06/10/26</td>
      <td>Party A; Party B; ...</td>
      <td>&nbsp;</td>

    Court Filing fk values are alphanumeric docket numbers ("CT-XXXX-26",
    "CH-26-XXXX"), unlike Tax Liens where fk is always a plain integer.
    """
    rows = page.query_selector_all(
        "table.data-table tbody tr, table.data-table tr:not(:first-child)"
    )
    results = []

    for row in rows:
        cells = row.query_selector_all("td")
        if len(cells) < 2:
            continue

        link = cells[0].query_selector("a[href*='OpenChild']")
        if not link:
            continue

        href = link.get_attribute("href") or ""
        match = re.search(r"OpenChild\('([^']+)','(\d+)'\)", href)
        if not match:
            continue

        results.append((match.group(1), match.group(2)))

    return results


def _get_divorce_detail(
    page: Page,
    fk: str,
    xid: str,
    target_date: date,
) -> dict | None:
    """
    Open one Court Filing detail page.  Return a record dict if the filing
    Type is "Divorce"; return None for all other types (contract, personal
    injury, hospital lien, etc.).

    Detail page field labels confirmed from live sample — note MDN's typo:
      Document No.  → docket number (no trailing colon on this label)
      Type:         → kept if the value starts with "Divorce" (case-insensitive),
                      e.g. "Divorce" or "Divorce With Children"
      Date:         → filing date in M/D/YYYY format
      Plantiff:     → plaintiff name  ← MDN's own typo, not ours
      Defendant:    → defendant name
    """
    detail_url = f"{DETAIL_URL}?fk={fk}&xid={xid}"
    logger.info("  Detail page --> %s", detail_url)

    try:
        page.goto(detail_url, wait_until="domcontentloaded")
        page.wait_for_selector("#record-details table.data-table", timeout=15000)
    except PlaywrightTimeoutError:
        logger.warning("Detail page timed out for fk=%s xid=%s — skipping", fk, xid)
        return None

    fields = _parse_detail_table(page)

    filing_type = fields.get("type", "").strip()
    if not filing_type.lower().startswith("divorce"):
        logger.info("  Not a divorce (Type=%r) — skipping", filing_type)
        return None

    plaintiff = fields.get("plantiff", fields.get("plaintiff", "")).strip()
    defendant = fields.get("defendant", "").strip()
    docket_number = fields.get("document no.", fk).strip()

    date_str = fields.get("date", "")
    try:
        filing_date = datetime.strptime(date_str, "%m/%d/%Y").date().isoformat()
    except ValueError:
        filing_date = target_date.isoformat()

    logger.info(
        "  DIVORCE — Type=%r  Plaintiff=%r  Defendant=%r  Docket=%s",
        filing_type, plaintiff, defendant, docket_number,
    )

    return {
        "filing_date": filing_date,
        "record_type": filing_type,
        "primary_name": plaintiff,
        "secondary_name": defendant,
        "docket_number": docket_number,
        "unverified_address": "-",
        "debt_amount": "",
    }


def _parse_detail_table(page: Page) -> dict:
    """
    Parse all label/value rows from the Court Filing: Record Details table.

    Row layout: <td><b>Label</b></td> <td>Value</td> <td>&nbsp;</td>
    Labels may or may not have a trailing colon — strip it before keying.
    """
    fields = {}
    rows = page.query_selector_all("#record-details table.data-table tr")
    for row in rows:
        cells = row.query_selector_all("td")
        if len(cells) < 2:
            continue
        label = cells[0].inner_text().strip().rstrip(":").lower()
        value = cells[1].inner_text().strip()
        if label:
            fields[label] = value
    return fields
