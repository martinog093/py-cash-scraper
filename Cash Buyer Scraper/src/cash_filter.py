"""
Cash sale detection.

Shelby County (Memphis TN):
  Step 1 — fast filter: mortgage_amount == 0 in the p2.php result row.
  Step 2 — confirm: no Deed of Trust filed by buyer within ±30 days via p3.php.

Bergen County (Bergen County NJ):
  No mortgage_amount in the index.  Only step 2 applies: search the Bergen
  BrowserView API for a mortgage (MTG, MTGMOD, COMTG, etc.) filed by the buyer
  within ±30 days of the deed recording date.  Requires an active Playwright
  page already loaded with reCAPTCHA v3 initialised.
"""

import logging
import time
from datetime import datetime, timedelta

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE_URL = "https://search.register.shelby.tn.us"
NAME_SEARCH_URL = f"{BASE_URL}/p3.php"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": BASE_URL,
}

# Crawl delay between name-search requests
CRAWL_DELAY_SECONDS = 31

DEFAULT_MIN_PRICE = 50_000.0


def filter_cash_sales_shelby(records: list[dict], min_price: float = DEFAULT_MIN_PRICE) -> list[dict]:
    """
    From a list of raw Shelby County deed records, return only confirmed cash sales.

    Step 1 (fast): mortgage_amount == 0 from the p2.php result.
    Step 2 (thorough): for records that pass step 1, confirm no Deed of Trust
    was recorded for this buyer within 30 days via a p3.php name search.

    Records below min_price are always excluded (default $50,000).
    """
    # Step 1: price floor + mortgage amount fast filter
    candidates = [
        r for r in records
        if r.get("purchase_price", 0) >= min_price
        and r.get("mortgage_amount", 0) == 0.0
    ]

    logger.info(
        "Cash filter step 1: %d/%d records pass (price >= $%s, mortgage_amount == 0)",
        len(candidates), len(records), f"{min_price:,.0f}",
    )

    if not candidates:
        return []

    # Step 2: cross-check for Deed of Trust via name search
    confirmed: list[dict] = []
    with httpx.Client(headers=HEADERS, timeout=60, follow_redirects=True) as client:
        for i, record in enumerate(candidates):
            buyer = record.get("buyer_name", "").strip()
            sale_date_str = record.get("sale_date", "")

            if not buyer or not sale_date_str:
                confirmed.append(record)
                continue

            try:
                sale_date = datetime.strptime(sale_date_str, "%m/%d/%Y").date()
            except ValueError:
                confirmed.append(record)
                continue

            # buyer_name may be "BUYER A; BUYER B" for joint purchases — check each,
            # respecting the crawl delay between every request (not just per-record)
            buyer_names = [b.strip() for b in buyer.split(";") if b.strip()]
            has_lien = False
            for j, name in enumerate(buyer_names):
                if _buyer_has_deed_of_trust(client, name, sale_date):
                    has_lien = True
                    break
                if j < len(buyer_names) - 1:
                    time.sleep(CRAWL_DELAY_SECONDS)

            if has_lien:
                logger.info(
                    "  EXCLUDED (financed): %s — Deed of Trust found within 30 days",
                    buyer,
                )
            else:
                confirmed.append(record)

            if i < len(candidates) - 1:
                time.sleep(CRAWL_DELAY_SECONDS)

    logger.info("Cash filter step 2: %d/%d confirmed cash sales", len(confirmed), len(candidates))
    return confirmed


def _buyer_has_deed_of_trust(
    client: httpx.Client,
    buyer_name: str,
    sale_date,
    window_days: int = 30,
) -> bool:
    """
    Search p3.php for any Deed of Trust filed by buyer_name within ±window_days
    of the sale date. Returns True if a lien is found (NOT a cash sale).
    """
    start = (sale_date - timedelta(days=window_days)).strftime("%m/%d/%Y")
    end   = (sale_date + timedelta(days=window_days)).strftime("%m/%d/%Y")

    # Split name: try "Last First" heuristic — p3.php uses param1=Last, param2=First
    parts = buyer_name.strip().split()
    if len(parts) >= 2:
        last  = parts[0]
        first = parts[1]
    else:
        last  = buyer_name
        first = ""

    payload = {
        "nametype":   "1",    # Grantee (buyer)
        "indextype":  "LR",   # Land Records
        "param1":     last,
        "param2":     first,
        "bdate":      start,
        "edate":      end,
        "itype":      "TD",   # Deed of Trust
        "stype":      "name",
        "searchType": "Pure Alpha Search",
        "linkformat": "noshow",
    }

    try:
        resp = client.post(NAME_SEARCH_URL, data=payload, timeout=45)
        resp.raise_for_status()
    except Exception as e:
        logger.warning("p3.php name search failed for '%s': %s — assuming no lien", buyer_name, e)
        return False

    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table", id="hit_list")
    if not table:
        return False

    rows = table.find_all("tr")
    return len(rows) > 1  # more than just the header row means a lien was found


# ── Bergen County cash filter ─────────────────────────────────────────────────

# Bergen County mortgage-type doc codes (from confirmed doctypes list)
BERGEN_MORTGAGE_CODES = "MTG,MTGMOD,COMTG,AMTG,COLMTG,ASSUM,SUMTG"

MIN_PURCHASE_PRICE_BERGEN = 50_000.0  # same floor as Shelby


def filter_cash_sales_bergen(records: list[dict], page) -> list[dict]:
    """
    From Bergen County deed records, return only likely cash sales.

    Bergen County index does not include purchase price or mortgage amount.
    Cash detection: for each deed, search the BrowserView API for a mortgage
    filed by the buyer within ±30 days. If none found → likely cash.

    `page` must be an active Playwright page already loaded at the BrowserView
    SPA (so reCAPTCHA v3 is initialised and cookies are set).

    Records with purchase_price == 0.0 cannot be price-filtered here; all
    deeds pass through and are flagged as potential cash buyers.
    """
    if not records:
        return []

    logger.info("Bergen cash filter: checking %d deed records for financing", len(records))

    confirmed: list[dict] = []
    for i, record in enumerate(records):
        buyer = record.get("buyer_name", "").strip()
        sale_date_str = record.get("sale_date", "")

        if not buyer or not sale_date_str:
            confirmed.append(record)
            continue

        try:
            sale_date = datetime.strptime(sale_date_str, "%Y-%m-%d").date()
        except ValueError:
            confirmed.append(record)
            continue

        # buyer_name may be "BUYER A; BUYER B" for joint purchases — check each
        buyer_names = [b.strip() for b in buyer.split(";") if b.strip()]
        has_mtg = any(
            _bergen_buyer_has_mortgage(page, name, sale_date) for name in buyer_names
        )
        if has_mtg:
            logger.info(
                "  EXCLUDED (financed): %s — mortgage found within 30 days", buyer
            )
        else:
            confirmed.append(record)

    logger.info(
        "Bergen cash filter: %d/%d records confirmed cash", len(confirmed), len(records)
    )
    return confirmed


def _bergen_buyer_has_mortgage(page, buyer_name: str, sale_date, window_days: int = 30) -> bool:
    """
    Search the Bergen BrowserView API for a mortgage filed by buyer_name
    within ±window_days of the deed date. Returns True if a mortgage is found.

    Uses the same page.evaluate(fetch()) approach as the main scraper so that
    reCAPTCHA v3 tokens are generated automatically by the browser.
    """
    from src.scrapers.bergen import _get_recaptcha_token, _post_json, API_URL

    from_dt = (sale_date - timedelta(days=window_days))
    to_dt   = (sale_date + timedelta(days=window_days))

    payload = {
        "Party":               buyer_name,
        "PartyTown":           "",
        "DocTypes":            BERGEN_MORTGAGE_CODES,
        "FromDate":            from_dt.strftime("%Y%m%d"),
        "ToDate":              to_dt.strftime("%Y%m%d"),
        "MaxRows":             0,
        "RowsPerPage":         0,    # explicit non-zero values cause HTTP 400
        "StartRow":            0,
        "RecaptchaResponseV3": _get_recaptcha_token(page),
    }

    try:
        data = _post_json(page, API_URL, payload)
    except Exception as e:
        logger.warning("Bergen mortgage check failed for '%s': %s — assuming no lien", buyer_name, e)
        return False

    if isinstance(data, list):
        return len(data) > 0
    if isinstance(data, dict):
        return len(data.get("results", [])) > 0
    return False
