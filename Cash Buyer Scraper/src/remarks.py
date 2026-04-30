"""
Analyst-facing remarks generation.

Combines buyer_history flip detection (both markets) with Assessor
lag/mismatch explanations (Shelby only -- assessor_* fields are only ever
populated for Memphis TN records) and a Hot-buyer purchase-cluster note.
"""

import re
from datetime import datetime

from src.normalize import names_share_tokens

FLIP_WINDOW_DAYS = 60      # "same property, different sale within N days"
RECENT_SALE_DAYS = 14      # below this, an Assessor mismatch = normal lag, not a flag

_ZIP_RE = re.compile(r"\b(\d{5})\b")


def generate_remarks(record: dict, history_rows: list[dict]) -> str:
    """
    record        -- the confirmed cash-sale record dict. For Shelby records
                      it already carries (set earlier in main.py):
                      assessor_owner_name, assessor_match_type,
                      assessor_sales_history, assessor_url, times_bought_90d,
                      priority.
    history_rows   -- buyer_history.get_purchase_history_for_address() for
                      this record's property_address, across all buyers/runs,
                      INCLUDING this record's own row.
    Returns a single " | "-joined string, or "" if nothing applies.
    """
    notes: list[str] = []
    notes += _flip_notes(record, history_rows)
    notes += _assessor_notes(record)
    notes += _hot_cluster_note(record, history_rows)
    return " | ".join(notes)


def _parse_sale_date(record: dict):
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(record.get("sale_date", ""), fmt).date()
        except ValueError:
            continue
    return None


def _flip_notes(record: dict, history_rows: list[dict]) -> list[str]:
    this_record_number = record.get("record_number", "")
    others = [h for h in history_rows if h.get("record_number") != this_record_number]
    if not others:
        return []

    this_date = _parse_sale_date(record)
    if this_date is None:
        return []

    within_window = []
    for h in others:
        h_date = _parse_row_date(h.get("sale_date", ""))
        if h_date is None:
            continue
        days_apart = abs((this_date - h_date).days)
        if days_apart <= FLIP_WINDOW_DAYS:
            within_window.append((h, h_date, days_apart))

    if not within_window:
        return []

    if len(within_window) >= 2:
        total_sales = len(within_window) + 1  # + this record itself
        return [
            f"Property flipped multiple times: {total_sales} sales within "
            f"{FLIP_WINDOW_DAYS} days -- verify current owner before contacting."
        ]

    other, other_date, days_apart = within_window[0]
    direction = "earlier" if other_date < this_date else "later"
    return [
        f"Possible flip: property also sold to {other.get('buyer_name', '')} on "
        f"{other_date.isoformat()} ({days_apart} days {direction}) -- verify current "
        f"owner before contacting."
    ]


def _parse_row_date(date_str: str):
    """buyer_history rows are stored ISO (post-migration), but tolerate
    MM/DD/YYYY defensively in case this runs against an unmigrated DB."""
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    return None


def _assessor_notes(record: dict) -> list[str]:
    match_type = record.get("assessor_match_type")
    if match_type is None:
        return []  # not a Shelby record / Assessor lookup didn't run

    if match_type == "none":
        return ["Assessor lookup found no match for this address -- verify address/parcel manually."]

    if match_type == "multiple":
        n = len(record.get("assessor_candidates", []) or [])
        return [f"Multiple Assessor parcels matched this address ({n} candidates) -- manual verification needed."]

    if match_type != "single":
        return []

    assessor_owner = record.get("assessor_owner_name", "")
    buyer_name = record.get("buyer_name", "")
    if not assessor_owner or names_share_tokens(assessor_owner, buyer_name):
        return []  # already matches -- nothing to flag

    this_date = _parse_sale_date(record)
    days_since = (datetime.now().date() - this_date).days if this_date else None

    sales_history = record.get("assessor_sales_history") or []
    record_number = record.get("record_number", "")
    already_in_history = any(record_number and record_number in sh.get("instrument", "") for sh in sales_history)
    if already_in_history:
        return [
            f"Note: Assessor Sales History already reflects this transaction (record "
            f"{record_number}) even though the Owner Name field has not updated yet."
        ]

    if days_since is None or days_since < RECENT_SALE_DAYS:
        return [
            f"Assessor still lists prior owner ({assessor_owner}) -- normal lag, county roll "
            f"typically updates within 2-4 weeks of recording."
        ]

    return [
        f"Assessor owner ({assessor_owner}) does not match deed buyer ({buyer_name}) and sale "
        f"is {days_since} days old -- verify before contacting; may indicate a flip or "
        f"unresolved title issue."
    ]


def _hot_cluster_note(record: dict, history_rows: list[dict]) -> list[str]:
    times_bought_90d = record.get("times_bought_90d", 0)
    if times_bought_90d < 2:
        return []

    buyer_name = record.get("buyer_name", "")
    zips = sorted({
        m.group(1)
        for h in history_rows
        for m in [_ZIP_RE.search(h.get("property_address", ""))]
        if m and names_share_tokens(h.get("buyer_name", ""), buyer_name)
    })
    zip_clause = f" across ZIPs {', '.join(zips)}" if len(zips) > 1 else ""
    return [f"Hot buyer: {times_bought_90d} purchases in last 90 days{zip_clause} -- high-volume investor."]
