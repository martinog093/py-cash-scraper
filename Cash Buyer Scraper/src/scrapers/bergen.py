"""
Bergen County Clerk land records scraper.

Portal:      https://bclrs.co.bergen.nj.us/
BrowserView: https://bclrs.co.bergen.nj.us/BrowserView/  (Angular SPA)
Search API:  POST https://bclrs.co.bergen.nj.us/browserview/api/search

Confirmed from DevTools inspection:
  - Endpoint: POST /browserview/api/search  (lowercase 'browserview' in URL)
  - Date format: YYYYMMDD  (e.g. "20260610")
  - Town field: PartyTown — uppercase portal value (e.g. "HACKENSACK")
  - Doc types: DocTypes — comma-separated codes (e.g. "BAR,DEED,DEED350,...")
  - Party name: Party — set to "" for doc-type-only searches
  - reCAPTCHA v3: RecaptchaResponseV3 — invisible token, required on every POST
    Site key: 6Leh8pMjAAAAANz-BGyN9lZzLAFsTN4hwI8KmnJn
    (confirmed from the actual <script src="...recaptcha/api.js?render=...">
    tag on the live page — an earlier key pulled from a disabled v2 widget
    in the static HTML was wrong and caused "No V3 token found" server errors)

reCAPTCHA v3 strategy:
  The token is generated silently by the browser using grecaptcha.execute().
  We load the SPA once in Playwright (which loads the reCAPTCHA scripts), then
  call the API directly via page.evaluate(fetch(...)) — the browser generates
  a fresh token for each request automatically via JS.
  This avoids DOM form interaction entirely and handles reCAPTCHA transparently.

Town values confirmed: common_town_value == common_town_name == UPPERCASE
  (e.g. "HACKENSACK", "TEANECK", "WASHINGTON TOWNSHIP")
  Pre-stored in data/bergen_zip_to_town.json.

Deed type codes confirmed from portal Document Type tree:
  BAR       — Bargain & Sale Deed  (standard NJ residential transfer)
  DEED      — Deed & Realty Tax Fees
  DEED350   — Deed > $350,000
  DEED1MIL  — Deed > $1,000,000
  SHER      — Sheriff's Deed  (foreclosure auction — almost always cash)
  DEEDFLCR  — Deed in Lieu of Foreclosure
  2         — Deed  (legacy code)
"""

import json
import logging
import os
from datetime import date, timedelta

logger = logging.getLogger(__name__)

BROWSERV_URL   = "https://bclrs.co.bergen.nj.us/BrowserView/"
API_URL        = "https://bclrs.co.bergen.nj.us/browserview/api/search"
RECAPTCHA_KEY  = "6Leh8pMjAAAAANz-BGyN9lZzLAFsTN4hwI8KmnJn"
ZIP_MAP_FILE   = "data/bergen_zip_to_town.json"

DEED_CODES = "BAR,DEED,DEED350,DEED1MIL,SHER,DEEDFLCR,2"

BERGEN_ZIPS = [
    "07601", "07602", "07603", "07604", "07605", "07606", "07607", "07608",
    "07620", "07621", "07624", "07626", "07627", "07628", "07630", "07631",
    "07632", "07640", "07641", "07642", "07643", "07644", "07645", "07646",
    "07647", "07648", "07649", "07650", "07652", "07653", "07656", "07657",
    "07660", "07661", "07662", "07663", "07666", "07670", "07675", "07676",
    "07677",
]


def scrape_bergen(
    days: int = 7,
    zip_code: str | None = None,
) -> list[dict]:
    """
    Scrape deed filings from Bergen County.

    Loads the BrowserView SPA once (initialises reCAPTCHA v3), then calls
    the REST API directly via page.evaluate(fetch()) for each municipality.
    Returns raw deed records (not cash-filtered).
    """
    towns      = _load_towns(zip_code)
    end_date   = date.today()
    start_date = end_date - timedelta(days=days)
    return _run_in_browser(_scrape_towns, towns=towns, start_date=start_date, end_date=end_date)


def run_bergen_pipeline(
    days: int = 7,
    zip_code: str | None = None,
) -> list[dict]:
    """
    Full Bergen County pipeline in a single browser session:
    scrape deed filings → cash filter → return confirmed cash buyers.

    Keeps the browser open across both steps so that reCAPTCHA v3 tokens
    are shared and the SPA is only loaded once.

    Cash filter performs a name-search for mortgage filings (MTG/MTGMOD/…)
    per buyer via the same BrowserView API.
    """
    from src.cash_filter import filter_cash_sales_bergen

    towns      = _load_towns(zip_code)
    end_date   = date.today()
    start_date = end_date - timedelta(days=days)

    def _pipeline(page) -> list[dict]:
        raw = _scrape_towns(page, towns=towns, start_date=start_date, end_date=end_date)
        logger.info("Bergen: %d raw deed records — running cash filter", len(raw))
        return filter_cash_sales_bergen(raw, page)

    return _run_in_browser(_pipeline)


def _run_in_browser(fn, **kwargs) -> list[dict]:
    """
    Launch a headless Chromium browser, load the BrowserView SPA, then
    call fn(page, **kwargs).  Closes the browser when done.
    """
    from playwright.sync_api import sync_playwright

    # Google's reCAPTCHA v3 detects headless Chromium and silently refuses to
    # issue a valid token (confirmed: server says "No V3 token found" because
    # our token comes back empty in headless mode). Running headed, plus
    # hiding the automation flag, gets a real token. The client runs this
    # locally with a real display, so a visible browser window is fine.
    proxy_url = os.getenv("PROXY_URL", "")
    launch_kwargs: dict = {
        "headless": False,
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    if proxy_url:
        launch_kwargs["proxy"] = {"server": proxy_url}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(**launch_kwargs)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            ignore_https_errors=True,
        )
        page = context.new_page()
        try:
            _load_spa(page)
            result = fn(page, **kwargs)
            return result if result is not None else []
        finally:
            browser.close()


def _scrape_towns(
    page,
    *,
    towns: list[str],
    start_date,
    end_date,
) -> list[dict]:
    """Iterate municipalities and collect deed records using an open page."""
    if not towns:
        logger.warning("Bergen: no municipalities — skipping")
        return []

    logger.info(
        "Bergen County: %d municipality/ies from %s to %s",
        len(towns), start_date, end_date,
    )

    all_records: list[dict] = []
    for i, town in enumerate(towns):
        logger.info("  Municipality: %s (%d/%d)", town, i + 1, len(towns))
        try:
            records = _search_town_via_api(page, town, start_date, end_date)
            logger.info("    -> %d deed records", len(records))
            all_records.extend(records)
        except Exception as e:
            logger.error("    Town '%s' failed: %s", town, e, exc_info=True)

    logger.info("Bergen County: %d total raw records", len(all_records))
    return all_records


# ── SPA loading ───────────────────────────────────────────────────────────────

def _load_spa(page) -> None:
    """
    Load the BrowserView Angular SPA and wait for reCAPTCHA v3 to initialise.
    The reCAPTCHA v3 library (grecaptcha) must be ready before we call the API.
    """
    from playwright.sync_api import TimeoutError as PWTimeout

    logger.info("Loading BrowserView SPA: %s", BROWSERV_URL)
    page.goto(BROWSERV_URL, wait_until="networkidle", timeout=60_000)

    # Confirm Angular bootstrapped (at least one search tab rendered)
    for selector in ('a:has-text("Document Type")', 'a:has-text("Party")', 'button:has-text("Search")'):
        try:
            page.wait_for_selector(selector, timeout=20_000)
            break
        except PWTimeout:
            continue
    else:
        raise RuntimeError(f"BrowserView SPA did not load. URL: {BROWSERV_URL}")

    # Wait for grecaptcha to be available in the page's JS context
    try:
        page.wait_for_function("typeof grecaptcha !== 'undefined'", timeout=15_000)
        logger.info("  SPA + reCAPTCHA v3 ready")
    except PWTimeout:
        logger.warning(
            "  grecaptcha not detected — proceeding without reCAPTCHA token. "
            "API calls may return 403 if the token is strictly required."
        )


# ── API search ────────────────────────────────────────────────────────────────

def _search_town_via_api(
    page,
    town: str,
    start_date: date,
    end_date: date,
) -> list[dict]:
    """
    Call the Bergen County search REST API for one municipality.

    Uses page.evaluate(fetch()) inside the real Chromium browser so that
    cookies and reCAPTCHA v3 are handled automatically.

    RowsPerPage is sent as 0 — this matches the actual working request
    captured from the live portal's DevTools. Sending an explicit value
    (e.g. 1000) causes the server to reject the request with HTTP 400.
    The server caps each response at _max_rows (observed as 1000) and
    reports it in the response; we paginate using that value.
    """
    from_str = start_date.strftime("%Y%m%d")
    to_str   = end_date.strftime("%Y%m%d")

    all_rows: list[dict] = []
    start_row = 0

    while True:
        payload = {
            "Party":               "",
            "PartyTown":           town,
            "DocTypes":            DEED_CODES,
            "FromDate":            from_str,
            "ToDate":              to_str,
            "MaxRows":             0,
            "RowsPerPage":         0,
            "StartRow":            start_row,
            "RecaptchaResponseV3": _get_recaptcha_token(page),
        }

        data = _post_json(page, API_URL, payload)

        # Response is a flat list; each row carries pagination metadata:
        #   _total_rows, _start_row, _end_row, _max_rows
        # Confirmed from live API response (DevTools capture).
        if data is None:
            break
        if isinstance(data, list):
            rows     = data
            total    = int(rows[0].get("_total_rows", 0)) if rows else 0
            max_rows = int(rows[0].get("_max_rows", 0)) if rows else 0
        elif isinstance(data, dict):
            # Fallback in case server wraps response in future
            rows     = data.get("results", [])
            total    = int(data.get("totalRows", data.get("_total_rows", 0)) or 0)
            max_rows = int(data.get("_max_rows", 0) or 0)
        else:
            logger.warning("    Unexpected API response type for %s: %s", town, type(data))
            break

        if not rows:
            break

        all_rows.extend(rows)

        page_size = max_rows or len(rows)
        fetched = start_row + len(rows)
        if len(rows) < page_size or (total and fetched >= total):
            break
        start_row = fetched

    return _parse_results(all_rows, town)


def _get_recaptcha_token(page) -> str:
    """
    Execute reCAPTCHA v3 in the browser and return the token string.
    Returns "" if grecaptcha is not available (will let the server decide).
    """
    try:
        token = page.evaluate(
            f"""() => new Promise((resolve, reject) => {{
                if (typeof grecaptcha === 'undefined') {{ resolve(''); return; }}
                grecaptcha.ready(() => {{
                    grecaptcha.execute('{RECAPTCHA_KEY}', {{action: 'submit'}})
                        .then(resolve)
                        .catch(() => resolve(''));
                }});
            }})"""
        )
        return token or ""
    except Exception as e:
        logger.debug("reCAPTCHA token error: %s", e)
        return ""


def _post_json(page, url: str, payload: dict) -> dict | None:
    """
    POST JSON to url using the browser's fetch() API.
    Returns the parsed JSON response, or None on error.
    """
    try:
        result = page.evaluate(
            """async ([url, body]) => {
                try {
                    const resp = await fetch(url, {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify(body)
                    });
                    if (!resp.ok) {
                        const text = await resp.text();
                        return {_error: resp.status, _body: text};
                    }
                    return await resp.json();
                } catch (e) {
                    return {_error: String(e)};
                }
            }""",
            [url, payload],
        )
        if isinstance(result, dict) and "_error" in result:
            logger.warning(
                "    API error for %s: %s — response body: %s",
                payload.get("PartyTown") or payload.get("Party"),
                result["_error"],
                result.get("_body", "")[:500],
            )
            return None
        return result
    except Exception as e:
        logger.error("    fetch() failed: %s", e)
        return None


# ── Result parsing ────────────────────────────────────────────────────────────

def _parse_results(rows: list, search_town: str) -> list[dict]:
    """
    Parse API response rows into standardised record dicts.

    Confirmed field names from live API response:
      doc_id          — internal document ID
      party_name      — the matched party name (leading/trailing spaces)
      cross_party_name— the other party on the document
      partyD_label    — "*" if this row is the FROM/Direct (grantor/seller) party
      partyR_label    — "*" if this row is the TO/Reverse (grantee/buyer) party
      rec_date        — ISO datetime string: "1988-04-19T00:00:00"
      doc_type        — document type code (e.g. "BAR", "DEED", "SHER")
      file_num        — instrument number (e.g. "BAR2025123456")
      town            — municipality (may be null for non-deed types)
      lot / block     — lot and block numbers (may be null)
      legal_1/2/3     — legal description lines (may be null)
      doc_status      — "V" = verified, other = not verified

    NOTE: consid_1 (purchase price) is NOT in the index results.
    It is only available via a separate document-detail API call.
    purchase_price is left as 0.0; cash detection uses mortgage cross-check.

    Each document produces one row per party involved.  We group by file_num
    and reconstruct seller + buyer from partyD_label / partyR_label.
    """
    from collections import defaultdict

    # Group all rows by instrument number
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if not isinstance(row, dict):
            continue
        fn = str(row.get("file_num", "")).strip()
        if fn:
            groups[fn].append(row)

    records: list[dict] = []

    for file_num, doc_rows in groups.items():
        ref = doc_rows[0]  # any row has the doc-level metadata

        # Sellers: rows where partyD_label == "*" (FROM / Direct / grantor)
        sellers = [
            r["party_name"].strip()
            for r in doc_rows
            if r.get("partyD_label") == "*" and r.get("party_name", "").strip()
        ]
        # Buyers: rows where partyR_label == "*" (TO / Reverse / grantee)
        buyers = [
            r["party_name"].strip()
            for r in doc_rows
            if r.get("partyR_label") == "*" and r.get("party_name", "").strip()
        ]

        # Fallback: if only one row and the other side is in cross_party_name
        if not sellers and buyers:
            cross = (ref.get("cross_party_name") or "").strip()
            if cross:
                sellers = [cross]
        if not buyers and sellers:
            cross = (ref.get("cross_party_name") or "").strip()
            if cross:
                buyers = [cross]

        # Parse date: "1988-04-19T00:00:00" → "1988-04-19"
        raw_date = str(ref.get("rec_date") or "")
        rec_date = raw_date.split("T")[0] if "T" in raw_date else raw_date

        # Build address from lot/block/town or legal description
        doc_town = (ref.get("town") or search_town or "").strip()
        lot      = str(ref.get("lot")   or "").strip()
        block    = str(ref.get("block") or "").strip()
        legal    = str(ref.get("legal_1") or ref.get("legal_2") or ref.get("legal_3") or "").strip()

        if lot and block:
            address = f"Lot {lot} Block {block} — {doc_town}"
        elif legal:
            address = f"{legal} — {doc_town}"
        else:
            address = doc_town

        records.append({
            "market":                "Bergen County NJ",
            "record_number":         file_num,
            "deed_type":             str(ref.get("doc_type") or "").strip(),
            "seller_name":           "; ".join(sellers),
            "buyer_name":            "; ".join(buyers),
            "sale_date":             rec_date,
            "purchase_price":        0.0,   # not in index; see note above
            "mortgage_amount":       None,  # cash filter checks for MTG via name search
            "property_address":      address,
            "buyer_mailing_address": "",
            "search_zip":            "",
        })

    return records


# ── Town list ─────────────────────────────────────────────────────────────────

def _load_towns(zip_code: str | None) -> list[str]:
    """
    Return deduplicated municipality list from the ZIP map.
    Values are pre-uppercased to match the portal's commonTowns exactly.
    """
    try:
        with open(ZIP_MAP_FILE) as f:
            zip_map: dict = json.load(f)
    except FileNotFoundError:
        logger.error("Bergen ZIP map not found: %s", ZIP_MAP_FILE)
        return []

    target_zips = [zip_code] if zip_code else BERGEN_ZIPS
    towns: list[str] = []
    seen:  set[str]  = set()

    for z in target_zips:
        for name in zip_map.get(z, []):
            if name and name not in seen:
                towns.append(name)
                seen.add(name)

    return towns
