import concurrent.futures
import logging
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

logger = logging.getLogger(__name__)

DOCKET_SETUP_URL = "https://prdata.shelbycountytn.gov/prweb/ck_public_qry_doct.cp_dktrpt_setup_idx"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def get_probate_parties(case_id: str, _retry: bool = False) -> dict:
    """
    Look up a probate case on the Shelby County docket report
    (prdata.shelbycountytn.gov) and return the deceased ("RE: MATTER") and
    petitioner/executor name(s) for that case.

    Runs in a separate thread so it can open its own sync_playwright context
    without conflicting with the one already open in the main script.

    Returns:
      - {"status": "Unavailable"} if the docket report site couldn't be
        reached or the results page didn't load
      - Otherwise: {"subject_name": str, "petitioner_names": list[str]} —
        subject_name is "" if no "RE: MATTER" row was found
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(_get_probate_parties_impl, case_id, _retry).result()


def _get_probate_parties_impl(case_id: str, _retry: bool = False) -> dict:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent=USER_AGENT,
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )

        try:
            url = f"{DOCKET_SETUP_URL}?case_id={case_id}&begin_date=&end_date="
            logger.info("  SOURCE (Docket Report) --> %s", url)
            page.goto(url, wait_until="load", timeout=45000)

            search_frame = page.frame(name="Big")
            if search_frame is None:
                raise RuntimeError("docket report search form ('Big' frame) not found")

            search_frame.wait_for_selector('input[type="submit"]', timeout=15000)

            with page.expect_navigation(wait_until="load", timeout=30000):
                search_frame.click('input[type="submit"]')

            results_frame = page.frame(name="main")
            if results_frame is None:
                raise RuntimeError("docket report results ('main' frame) not found")

            results_frame.wait_for_selector("table", timeout=15000)
            parties = _parse_parties_table(results_frame)

        except (PlaywrightTimeoutError, RuntimeError) as e:
            browser.close()
            if not _retry:
                logger.warning("Docket report lookup for %s failed — retrying once: %s", case_id, e)
                return _get_probate_parties_impl(case_id, _retry=True)
            logger.error("Docket report lookup for %s failed: %s", case_id, e)
            return {"status": "Unavailable"}

        browser.close()
        return parties


def _parse_parties_table(frame) -> dict:
    """
    Parse the case parties table on the docket report results page.

    Header row: ['Seq #', 'Assoc', 'Party End Date', 'Type', 'ID', 'Name']
    Each party is one 6-cell data row, e.g.:
      ['2', '', '', 'RE: MATTER', 'BRO041726', 'BROWN, SUZANNE C']
    followed by an "Address: ... Aliases: ..." row and a blank row.

    "RE: MATTER" is the deceased / subject of the probate case.
    "PETITIONER" is the person who filed the petition (potential heir) — a
    case can have more than one.
    "ATTORNEY" / "ATTY" is the attorney representing the petitioner.
    """
    subject_names: list[str] = []
    petitioner_names: list[str] = []
    attorney_names: list[str] = []

    for table in frame.query_selector_all("table"):
        rows = table.query_selector_all("tr")
        if not rows:
            continue

        header = [c.inner_text().strip() for c in rows[0].query_selector_all("th, td")]
        if header[:2] != ["Seq #", "Assoc"]:
            continue

        for row in rows[1:]:
            cells = row.query_selector_all("td")
            if len(cells) != 6:
                continue
            party_type = cells[3].inner_text().strip().upper()
            name = cells[5].inner_text().strip()
            if not name:
                continue
            if party_type == "RE: MATTER":
                subject_names.append(name)
            elif party_type == "PETITIONER":
                petitioner_names.append(name)
            elif "ATTY" in party_type or "ATTORNEY" in party_type:
                attorney_names.append(name)

    return {
        "subject_name": subject_names[0] if subject_names else "",
        "petitioner_names": petitioner_names,
        "attorney_names": attorney_names,
    }
