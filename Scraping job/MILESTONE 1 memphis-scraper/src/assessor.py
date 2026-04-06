import concurrent.futures
import logging
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

logger = logging.getLogger(__name__)

SEARCH_URL = "https://assessormelvinburgess.com/propertySearch"
OWNER_SUBMIT_URL = "https://assessormelvinburgess.com/OwnerSubmit"
RESULTS_TABLE_SELECTOR = "table tbody tr"


def _search_owner(first_name: str, last_name: str, _retry: bool = False) -> list[dict]:
    """
    Submit an owner-name search and return a list of matching records.
    Runs in a separate thread so it can open its own sync_playwright context
    without conflicting with the one already open in the calling script.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(_search_owner_impl, first_name, last_name, _retry).result()


def _search_owner_impl(first_name: str, last_name: str, _retry: bool = False) -> list[dict]:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"
        )

        try:
            page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector('input[name="firstName"]', timeout=15000)

            page.fill('input[name="firstName"]', first_name)
            page.fill('input[name="lastName"]', last_name)

            # Click the Submit button inside the owner name form
            page.evaluate('''() => {
                const fn = document.querySelector('input[name="firstName"]');
                const form = fn && fn.closest("form");
                const btn = form && form.querySelector('button[type="submit"], button');
                if (btn) btn.click();
            }''')

            # Wait for results table to appear (portal can be slow)
            page.wait_for_selector(RESULTS_TABLE_SELECTOR, timeout=45000)

            results_url = page.url
            logger.info("  SOURCE (Assessor) --> %s", results_url)

        except PlaywrightTimeoutError:
            logger.warning("Assessor search timed out for %s %s — retrying once...", first_name, last_name)
            browser.close()
            return _search_owner_impl(first_name, last_name, _retry=True) if not _retry else []

        # A single exact match redirects straight to that property's detail
        # page (propertyDetails?...parcelid=...) instead of the multi-row
        # search results list (realPropertyDetails?...&Page=property). The
        # detail page has several "label: value" tables plus a Sales History
        # table — both match "table tbody tr", so it needs its own parser.
        url_lower = results_url.lower()
        if "propertydetails" in url_lower and "realpropertydetails" not in url_lower:
            results = _parse_property_detail_page(page)
        else:
            results = _parse_results_table(page)
        browser.close()
        return results


def _parse_results_table(page) -> list[dict]:
    """Parse the results table: Parcel ID | Owner Name | Property Location | Link"""
    rows = page.query_selector_all(RESULTS_TABLE_SELECTOR)
    results = []
    for row in rows:
        cells = row.query_selector_all("td")
        if len(cells) < 3:
            continue
        parcel_id = cells[0].inner_text().strip()
        owner_name = cells[1].inner_text().strip()
        address = cells[2].inner_text().strip()
        if parcel_id or address:
            results.append({
                "parcel_id": parcel_id,
                "owner_name": owner_name,
                "verified_address": address,
            })
    return results


def _parse_property_detail_page(page) -> list[dict]:
    """
    Parse a single-property detail page (propertyDetails?...parcelid=...),
    reached when the owner-name search has exactly one match. The page has
    several "label: value" tables — the first contains Parcel ID, Property
    Address, and Owner Name among other fields. Returns a single-result list
    in the same shape as _parse_results_table.
    """
    fields = {}
    for row in page.query_selector_all("table tbody tr"):
        cells = row.query_selector_all("td")
        if len(cells) != 2:
            continue
        label = cells[0].inner_text().strip().rstrip(":").strip().lower()
        value = cells[1].inner_text().strip()
        fields.setdefault(label, value)

    parcel_id = fields.get("parcel id", "")
    address = fields.get("property address", "")
    owner_name = fields.get("owner name", "")

    if not parcel_id and not address:
        return []

    return [{
        "parcel_id": parcel_id,
        "owner_name": owner_name,
        "verified_address": address,
    }]


def _split_name(full_name: str) -> tuple[str, str]:
    """Split 'Last, First' or 'First Last' into (first, last)."""
    full_name = full_name.strip()
    if "," in full_name:
        parts = [p.strip() for p in full_name.split(",", 1)]
        return parts[1], parts[0]  # first, last
    parts = full_name.split()
    if len(parts) >= 2:
        return parts[0], parts[-1]
    return full_name, ""


def _name_match_score(mdn_name: str, owner_name: str) -> int:
    """Count of name tokens shared between the MDN name and an Assessor owner_name."""
    mdn_tokens = {t.lower().rstrip(".,") for t in mdn_name.replace(",", " ").split()}
    owner_tokens = {t.lower().rstrip(".,") for t in owner_name.replace(",", " ").split()}
    return len(mdn_tokens & owner_tokens)


def _narrow_by_name_match(mdn_name: str, results: list[dict]) -> list[dict]:
    """
    When the same first/last name returns multiple Assessor records, prefer
    the one(s) whose owner_name shares the most tokens with the full MDN
    name — e.g. for MDN name "Jawwad A Ahmed", prefer Assessor owner_name
    "AHMED JAWWAD A" (matches first, middle initial, and last) over
    "AHMED JAWWAD" (matches only first and last).

    If multiple results tie for the best score, no narrowing is done — they
    remain genuinely ambiguous and are all listed for review.
    """
    if len(results) <= 1:
        return results

    scored = [(r, _name_match_score(mdn_name, r.get("owner_name", ""))) for r in results]
    best_score = max(score for _, score in scored)
    narrowed = [r for r, score in scored if score == best_score]

    if len(narrowed) < len(results):
        logger.info(
            "Narrowed %d candidates for '%s' to %d using full-name match",
            len(results), mdn_name, len(narrowed),
        )
        return narrowed

    return results


def verify_ownership(name: str, mdn_address: str) -> dict | None:
    """
    Search the Assessor portal by owner name.

    Returns None if the Assessor has no property record under this name
    (i.e. not a Shelby County property owner — record should be discarded).

    Otherwise returns a dict with:
      - status: "Verified" if the MDN tax-lien address matches one of the
        Assessor's results for this name (or only one result exists and no
        MDN address was given), otherwise "Needs Review"
      - verified_address / parcel_id: on "Verified", the matching Assessor
        record. On "Needs Review", every remaining candidate address /
        parcel ID for this name, semicolon-separated — listed rather than
        guessed because we can't tell which (if any) is the person from the
        tax lien
      - unverified_address: "-" on "Verified", otherwise the original MDN
        tax-lien address — flagged because it didn't match any Assessor
        record for this name and may belong to a different party/property

    Or {"status": "Assessor Unavailable"} if the portal is unreachable.
    """
    logger.info("Verifying ownership for name='%s' address='%s'", name, mdn_address)
    try:
        first, last = _split_name(name)
        results = _search_owner(first, last)
    except Exception as e:
        logger.error("Assessor search failed for '%s': %s", name, e)
        return {"status": "Assessor Unavailable"}

    if not results:
        logger.info("No assessor results for name='%s'", name)
        return None

    # If the same first/last name matched several owners, narrow down to the
    # one(s) whose full name (incl. middle name/initial) best matches before
    # falling back to address matching / "Needs Review".
    results = _narrow_by_name_match(name, results)

    mdn_norm = mdn_address.lower().strip()
    if mdn_norm:
        for r in results:
            result_addr = r.get("verified_address", "").lower().strip()
            if result_addr and _addresses_match(mdn_norm, result_addr):
                return {
                    "status": "Verified",
                    "verified_address": r["verified_address"],
                    "parcel_id": r["parcel_id"],
                    "unverified_address": "-",
                }

    if not mdn_norm and len(results) == 1:
        r = results[0]
        return {
            "status": "Verified",
            "verified_address": r["verified_address"],
            "parcel_id": r["parcel_id"],
            "unverified_address": "-",
        }

    # Either the MDN address didn't match any candidate, or there's no MDN
    # address to disambiguate between multiple candidates — list every
    # candidate rather than guessing which one is the right person.
    if mdn_norm:
        logger.info(
            "MDN address '%s' did not match any assessor result for '%s' — flagging for review",
            mdn_address, name,
        )
    if len(results) > 1:
        logger.warning("Multiple assessor results for '%s' — listing all %d candidates", name, len(results))

    return {
        "status": "Needs Review",
        "verified_address": "; ".join(r["verified_address"] for r in results),
        "parcel_id": "; ".join(r["parcel_id"] for r in results),
        "unverified_address": mdn_address if mdn_norm else "-",
    }


def search_by_name(name: str) -> dict | None:
    """
    Name-only search — no address cross-check. Used by Probate (and Divorce,
    M2), where the source data has no address to verify against.

    Returns:
      - None if the Assessor has no property under this name (record should
        be discarded)
      - {"status": "Assessor Unavailable"} if the portal is unreachable
      - {"status": "Verified", "verified_address": ..., "parcel_id": ...} if
        exactly one candidate remains after narrowing by full-name match
      - {"status": "Needs Review", "verified_address": "a; b", "parcel_id": "x; y"}
        if multiple candidates remain — every candidate is listed,
        semicolon-separated, rather than guessing which one is correct
    """
    logger.info("Name-only assessor search for '%s'", name)
    try:
        first, last = _split_name(name)
        results = _search_owner(first, last)
    except Exception as e:
        logger.error("Assessor search failed for '%s': %s", name, e)
        return {"status": "Assessor Unavailable"}

    if not results:
        return None

    results = _narrow_by_name_match(name, results)

    if len(results) == 1:
        r = results[0]
        return {
            "status": "Verified",
            "verified_address": r["verified_address"],
            "parcel_id": r["parcel_id"],
        }

    logger.warning("Multiple assessor results for '%s' — listing all %d candidates", name, len(results))
    return {
        "status": "Needs Review",
        "verified_address": "; ".join(r["verified_address"] for r in results),
        "parcel_id": "; ".join(r["parcel_id"] for r in results),
    }


def _addresses_match(a: str, b: str) -> bool:
    """Fuzzy token-overlap address match (>=50% shared meaningful tokens)."""
    noise = {"st", "rd", "ave", "blvd", "dr", "ln", "ct", "tn", "memphis", "#"}
    tokens_a = set(a.split()) - noise
    tokens_b = set(b.split()) - noise
    if not tokens_a or not tokens_b:
        return False
    overlap = tokens_a & tokens_b
    return len(overlap) / max(len(tokens_a), len(tokens_b)) >= 0.5
