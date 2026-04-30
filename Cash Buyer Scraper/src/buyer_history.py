"""
SQLite-backed buyer history — persists across weekly runs.
Used to compute times_bought_90d and Hot/Warm/Standard priority.
"""

import logging
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta

from src.normalize import normalize_name

logger = logging.getLogger(__name__)

DB_PATH = "data/buyer_history.db"


def init_db(db_path: str = DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS purchases (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                market          TEXT    NOT NULL,
                buyer_name      TEXT    NOT NULL,
                buyer_name_key  TEXT,
                property_address TEXT   NOT NULL,
                sale_date       TEXT    NOT NULL,
                purchase_price  REAL    NOT NULL,
                record_number   TEXT    NOT NULL UNIQUE
            )
        """)
        _migrate_schema(conn)
        conn.commit()
    logger.info("Buyer history DB ready at %s", db_path)


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """
    Idempotent, self-healing migration run on every init_db() call.

    1. Add buyer_name_key if this DB predates it (ALTER TABLE ADD COLUMN has
       no IF NOT EXISTS in SQLite, so guard via PRAGMA table_info).
    2. Backfill buyer_name_key AND re-normalize sale_date to ISO for every
       row where buyer_name_key IS NULL. Required for real historical data:
       Shelby rows were stored "MM/DD/YYYY" while Bergen rows were stored
       ISO "YYYY-MM-DD" -- comparing a MM/DD/YYYY string against an ISO
       cutoff with plain SQL >= is always False (the first character is
       always the month, '0' or '1', which is lexicographically less than
       any '20XX' year), so every Shelby buyer's times_bought_90d has
       always been 0 regardless of actual recency. Without this backfill,
       old rows would keep failing every 90-day query forever even after
       the code below is fixed, since the fix only changes how NEW rows are
       read/written.
    3. Index buyer_name_key for the lookup added in count_buyer_purchases_90d.
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(purchases)").fetchall()}
    if "buyer_name_key" not in cols:
        conn.execute("ALTER TABLE purchases ADD COLUMN buyer_name_key TEXT")

    stale_rows = conn.execute(
        "SELECT id, buyer_name, sale_date FROM purchases WHERE buyer_name_key IS NULL"
    ).fetchall()
    for row_id, buyer_name, sale_date in stale_rows:
        conn.execute(
            "UPDATE purchases SET buyer_name_key = ?, sale_date = ? WHERE id = ?",
            (normalize_name(buyer_name), _normalize_date_to_iso(sale_date), row_id),
        )
    if stale_rows:
        logger.info("Buyer history migration: backfilled %d row(s)", len(stale_rows))

    conn.execute("CREATE INDEX IF NOT EXISTS idx_buyer_name_key ON purchases(buyer_name_key)")


def _normalize_date_to_iso(date_str: str) -> str:
    """Try Shelby's 'MM/DD/YYYY' format, then fall back to already-ISO
    'YYYY-MM-DD' (Bergen's format / already-migrated rows). Never raises --
    logs a warning and returns the original string unchanged on total
    failure, so one malformed historical row can't break the migration."""
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str, fmt).date().isoformat()
        except ValueError:
            continue
    logger.warning("Could not parse sale_date %r during migration -- leaving as-is", date_str)
    return date_str


def insert_records(records: list[dict], db_path: str = DB_PATH) -> int:
    """
    Insert new confirmed cash sale records. Skips duplicates (by record_number).
    Returns number of newly inserted rows.
    """
    inserted = 0
    with _connect(db_path) as conn:
        for r in records:
            try:
                buyer_name = r.get("buyer_name", "")
                conn.execute(
                    """
                    INSERT OR IGNORE INTO purchases
                        (market, buyer_name, buyer_name_key, property_address, sale_date, purchase_price, record_number)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        r.get("market", ""),
                        buyer_name,
                        normalize_name(buyer_name),
                        r.get("property_address", ""),
                        _normalize_date_to_iso(r.get("sale_date", "")),
                        r.get("purchase_price", 0.0),
                        r.get("record_number", ""),
                    ),
                )
                if conn.execute("SELECT changes()").fetchone()[0]:
                    inserted += 1
            except Exception as e:
                logger.warning("Failed to insert record %s: %s", r.get("record_number"), e)
        conn.commit()
    logger.info("Inserted %d new record(s) into buyer history", inserted)
    return inserted


def count_buyer_purchases_90d(buyer_name: str, db_path: str = DB_PATH) -> int:
    """Return how many times this buyer appears in the last 90 days."""
    cutoff = (date.today() - timedelta(days=90)).isoformat()
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) FROM purchases
            WHERE buyer_name_key = ?
              AND sale_date >= ?
            """,
            (normalize_name(buyer_name), cutoff),
        ).fetchone()
    return row[0] if row else 0


def enrich_with_history(records: list[dict], db_path: str = DB_PATH) -> list[dict]:
    """Add times_bought_90d to each record (queries the DB once per unique buyer)."""
    cache: dict[str, int] = {}
    for r in records:
        key = normalize_name(r.get("buyer_name", ""))
        if key not in cache:
            cache[key] = count_buyer_purchases_90d(r["buyer_name"], db_path)
        r["times_bought_90d"] = cache[key]
    return records


def get_purchase_history_for_address(property_address: str, db_path: str = DB_PATH) -> list[dict]:
    """
    Return every purchases row (any buyer, any market, any historical run)
    recorded for this exact property_address, ordered by sale_date --
    buyer-agnostic, unlike count_buyer_purchases_90d. Used for flip
    detection: a property can flip between two DIFFERENT buyers within
    days, which a buyer-keyed query would never surface.

    Matches on UPPER(TRIM(property_address)) -- property_address is already
    built as a consistent concatenated string by both scrapers, so no new
    normalized-address column is needed. Returns [] immediately for a blank
    address, since multiple blank-address rows would otherwise all "match"
    each other as a false flip.
    """
    address = (property_address or "").strip()
    if not address:
        return []
    with _connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT market, buyer_name, property_address, sale_date, purchase_price, record_number
            FROM purchases
            WHERE UPPER(TRIM(property_address)) = UPPER(TRIM(?))
            ORDER BY sale_date
            """,
            (address,),
        ).fetchall()
    return [dict(row) for row in rows]


@contextmanager
def _connect(db_path: str):
    conn = sqlite3.connect(db_path)
    try:
        yield conn
    finally:
        conn.close()
