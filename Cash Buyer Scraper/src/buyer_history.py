"""
SQLite-backed buyer history — persists across weekly runs.
Used to compute times_bought_90d and Hot/Warm/Standard priority.
"""

import logging
import sqlite3
from contextlib import contextmanager
from datetime import date, timedelta

logger = logging.getLogger(__name__)

DB_PATH = "data/buyer_history.db"


def init_db(db_path: str = DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS purchases (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                market          TEXT    NOT NULL,
                buyer_name      TEXT    NOT NULL,
                property_address TEXT   NOT NULL,
                sale_date       TEXT    NOT NULL,
                purchase_price  REAL    NOT NULL,
                record_number   TEXT    NOT NULL UNIQUE
            )
        """)
        conn.commit()
    logger.info("Buyer history DB ready at %s", db_path)


def insert_records(records: list[dict], db_path: str = DB_PATH) -> int:
    """
    Insert new confirmed cash sale records. Skips duplicates (by record_number).
    Returns number of newly inserted rows.
    """
    inserted = 0
    with _connect(db_path) as conn:
        for r in records:
            try:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO purchases
                        (market, buyer_name, property_address, sale_date, purchase_price, record_number)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        r.get("market", ""),
                        r.get("buyer_name", ""),
                        r.get("property_address", ""),
                        r.get("sale_date", ""),
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
            WHERE LOWER(buyer_name) = LOWER(?)
              AND sale_date >= ?
            """,
            (buyer_name, cutoff),
        ).fetchone()
    return row[0] if row else 0


def enrich_with_history(records: list[dict], db_path: str = DB_PATH) -> list[dict]:
    """Add times_bought_90d to each record (queries the DB once per unique buyer)."""
    cache: dict[str, int] = {}
    for r in records:
        buyer = r.get("buyer_name", "").strip().lower()
        if buyer not in cache:
            cache[buyer] = count_buyer_purchases_90d(r["buyer_name"], db_path)
        r["times_bought_90d"] = cache[buyer]
    return records


@contextmanager
def _connect(db_path: str):
    conn = sqlite3.connect(db_path)
    try:
        yield conn
    finally:
        conn.close()
