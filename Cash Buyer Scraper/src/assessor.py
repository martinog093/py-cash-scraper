"""
Shelby County Assessor (assessormelvinburgess.com) parcel lookup -- address search.

Shelby-only: no Bergen County Assessor equivalent exists. Not related to (and
does not import) the owner-name-search assessor.py used by the separate
Memphis Daily News tax-lien/divorce/probate scraper project -- that's a
different portal-search mode (name, not address) for a different pipeline.

Cloudflare returns 403 to plain httpx/curl; a genuine Playwright browser
navigation works fine, and headless=True is fine for this site (unlike
Bergen County, which needs a headed browser for reCAPTCHA v3 token issuance).
"""

import logging
import time
import urllib.parse

logger = logging.getLogger(__name__)

SEARCH_URL = "https://assessormelvinburgess.com/propertySearch"
DETAIL_URL_TMPL = "https://assessormelvinburgess.com/propertyDetails?IR=true&parcelid={parcelid}"

MAX_RETRIES = 2
RETRY_DELAY_SECONDS = 5
INTER_LOOKUP_DELAY_SECONDS = 2  # defensive pacing; no crawl-delay is mandated for this site

DIRECTIONS = {"N", "S", "E", "W", "NORTH", "SOUTH", "EAST", "WEST"}
STREET_SUFFIXES = {
    "ST", "AVE", "RD", "DR", "LN", "CV", "CT", "BLVD", "CIR", "PL", "WAY",
    "TRL", "LOOP", "PKWY", "TER", "PT", "HWY", "XING", "SQ", "PLZ", "RUN",
}


def parse_street(property_address: str) -> tuple[str, str]:
    """
    Split a property_address string (e.g. "134 WEST HOLMES RD MEMPHIS TN
    38109") into (stNumber, stName) for the Assessor's propertySearch form.

    The portal's address search matches the bare core street name only --
    a leading directional (WEST/W) or trailing suffix (RD/ST/AVE) in the
    query returns ZERO rows even though the property exists (confirmed
    empirically: "WEST HOLMES RD" -> 0 rows, "HOLMES" -> correct match). So
    strip exactly one leading directional token and one trailing suffix
    token before searching.

    Known, accepted gaps: a multi-word compound suffix, or a trailing
    directional AFTER a suffix (e.g. "PECAN CREEK CIR NORTH"), isn't
    stripped by this single-token-each-end logic -- such addresses surface
    downstream as a "no match" result, not a silent wrong answer.
    """
    tokens = property_address.split()
    if not tokens:
        return "", ""
    street_num = tokens[0]
    try:
        city_idx = tokens.index("MEMPHIS")
    except ValueError:
        city_idx = len(tokens) - 2 if len(tokens) >= 2 else len(tokens)
    name_tokens_ = tokens[1:city_idx]

    if name_tokens_ and name_tokens_[0].upper() in DIRECTIONS:
        name_tokens_ = name_tokens_[1:]
    if name_tokens_ and name_tokens_[-1].upper() in STREET_SUFFIXES:
        name_tokens_ = name_tokens_[:-1]

    return street_num, " ".join(name_tokens_)


def lookup_parcels(addresses: list[str], headless: bool = True) -> dict[str, dict]:
    """
    Look up Assessor parcel info for a batch of property_address strings,
    reusing ONE headless Chromium browser + page across all lookups (same
    reuse rationale as bergen.py's single-session pattern -- avoids
    per-lookup browser-launch overhead; this site has no reCAPTCHA so there
    is no token-freshness reason to restart the browser per request).

    Input addresses are de-duplicated before lookup -- a flipped property
    can appear twice in the same run's confirmed-cash-sale list under two
    different buyers, and should only be queried once.

    Returns a dict keyed by the ORIGINAL property_address string. Each value
    is one of:
      {}                                     -- lookup failed after retries
      {"match_type": "single", "parcel_id": ..., "owner_name": ...,
       "owner_mailing_address": ..., "owner_city_state_zip": ...,
       "assessor_property_address": ..., "sales_history": [...],
       "assessor_url": ...}
      {"match_type": "multiple", "candidates": [...]}   -- unresolved after narrowing
      {"match_type": "none"}                            -- portal reports no match
    """
    if not addresses:
        return {}

    unique_addresses = list(dict.fromkeys(addresses))  # de-dupe, preserve order
    results: dict[str, dict] = {}

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        page = browser.new_page(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
        )
        try:
            for i, address in enumerate(unique_addresses):
                logger.info("Assessor lookup [%d/%d]: %s", i + 1, len(unique_addresses), address)
                results[address] = _lookup_one(page, address)
                if i < len(unique_addresses) - 1:
                    time.sleep(INTER_LOOKUP_DELAY_SECONDS)
        finally:
            browser.close()

    return results


def _lookup_one(page, property_address: str, _retry_count: int = 0) -> dict:
    """Single address lookup on an already-open page, with retry on
    transient failures (the Assessor site is occasionally flaky -- an
    intermittent Cloudflare 520 upstream error was observed once and
    resolved on retry a few seconds later)."""
    street_num, street_name = parse_street(property_address)
    if not street_num:
        return {}

    try:
        page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_selector('input[name="stNumber"]', timeout=15000)
        page.fill('input[name="stNumber"]', street_num)
        page.fill('input[name="stName"]', street_name)
        with page.expect_navigation(timeout=20000):
            page.evaluate('''() => {
                const fn = document.querySelector('input[name="stNumber"]');
                const form = fn && fn.closest("form");
                const btn = form && form.querySelector('button[type="submit"], button');
                if (btn) btn.click();
            }''')
        page.wait_for_timeout(1000)

        url = page.url
        if "error=true" in url:
            return {"match_type": "none"}
        if "propertyDetails" in url and "realPropertyDetails" not in url:
            page.wait_for_selector("table tbody tr", timeout=15000)
            return _parse_single_match_page(page, property_address)
        if "realPropertyDetails" in url:
            page.wait_for_selector("table tbody tr", timeout=15000)
            return _parse_multi_match_page(page, property_address)
        return {"match_type": "none"}
    except Exception as e:
        if _retry_count < MAX_RETRIES:
            logger.warning("Assessor lookup failed (attempt %d) for %r: %s -- retrying",
                            _retry_count + 1, property_address, e)
            time.sleep(RETRY_DELAY_SECONDS)
            return _lookup_one(page, property_address, _retry_count=_retry_count + 1)
        logger.error("Assessor lookup failed permanently for %r: %s", property_address, e)
        return {}


def _parse_single_match_page(page, property_address: str) -> dict:
    """Single exact-match address search redirects to
    propertyDetails?parcelid=... First <table> has 2-column label:value
    fields (Parcel ID, Property Address, Owner Name, Owner Mailing Address,
    Owner City/State/Zip, ...). Last <table> is Sales History
    (Date | Price | Instrument# | Type)."""
    tables = page.query_selector_all("table")
    if not tables:
        return {"match_type": "none"}

    fields: dict[str, str] = {}
    for row in tables[0].query_selector_all("tbody tr"):
        cells = row.query_selector_all("td")
        if len(cells) != 2:
            continue
        label = cells[0].inner_text().strip().rstrip(":").strip().lower()
        value = cells[1].inner_text().strip()
        fields.setdefault(label, value)

    parcel_id = fields.get("parcel id", "")
    address = fields.get("property address", "")
    if not parcel_id and not address:
        return {"match_type": "none"}

    sales_history = []
    if len(tables) >= 2:
        for row in tables[-1].query_selector_all("tbody tr"):
            cells = row.query_selector_all("td")
            if len(cells) >= 4:
                sales_history.append({
                    "date": cells[0].inner_text().strip(),
                    "price": cells[1].inner_text().strip(),
                    "instrument": cells[2].inner_text().strip(),
                    "type": cells[3].inner_text().strip(),
                })

    return {
        "match_type": "single",
        "parcel_id": parcel_id,
        "owner_name": fields.get("owner name", ""),
        "owner_mailing_address": fields.get("owner mailing address", ""),
        "owner_city_state_zip": fields.get("owner city/state/zip", ""),
        "assessor_property_address": address,
        "sales_history": sales_history,
        "assessor_url": DETAIL_URL_TMPL.format(parcelid=urllib.parse.quote(parcel_id)),
    }


def _parse_multi_match_page(page, property_address: str) -> dict:
    """Multi-match search stays on realPropertyDetails with a results table:
    Parcel ID | Owner Name | Property Location | Link. Narrow by
    token-overlap against the full original property_address; if exactly
    one candidate survives, promote to a "single" match result."""
    rows = page.query_selector_all("table tbody tr")
    candidates = []
    for row in rows:
        cells = row.query_selector_all("td")
        if len(cells) < 3:
            continue
        candidates.append({
            "parcel_id": cells[0].inner_text().strip(),
            "owner_name": cells[1].inner_text().strip(),
            "property_location": cells[2].inner_text().strip(),
        })

    if not candidates:
        return {"match_type": "none"}

    narrowed = _narrow_multi_match(candidates, property_address)
    if len(narrowed) == 1:
        c = narrowed[0]
        return {
            "match_type": "single",
            "parcel_id": c["parcel_id"],
            "owner_name": c["owner_name"],
            "owner_mailing_address": "",
            "owner_city_state_zip": "",
            "assessor_property_address": c["property_location"],
            "sales_history": [],
            "assessor_url": DETAIL_URL_TMPL.format(parcelid=urllib.parse.quote(c["parcel_id"])),
        }

    return {"match_type": "multiple", "candidates": narrowed}


def _narrow_multi_match(candidates: list[dict], property_address: str) -> list[dict]:
    """When the address search returns multiple rows, narrow to the
    candidate(s) whose Property Location shares the most tokens with the
    full original property_address (numbers + remaining tokens). If exactly
    one survives, the caller promotes it to a resolved single match; if 0 or
    2+ survive, return the full original list unnarrowed -- genuinely
    ambiguous, list all candidates rather than guess."""
    from src.normalize import name_tokens

    if len(candidates) <= 1:
        return candidates

    addr_tokens = name_tokens(property_address)
    scored = [
        (c, len(addr_tokens & name_tokens(c["property_location"])))
        for c in candidates
    ]
    best_score = max(score for _, score in scored)
    narrowed = [c for c, score in scored if score == best_score]

    if len(narrowed) < len(candidates):
        logger.info("Narrowed %d Assessor candidates to %d using address-token match",
                     len(candidates), len(narrowed))
        return narrowed
    return candidates
