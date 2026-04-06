"""
Priority scoring and entity type detection.

Priority:
  Hot      — buyer appears 2+ times in last 90 days
  Warm     — LLC/entity buyer with single purchase over $150,000
  Standard — everything else

Entity type:
  "LLC"        — name contains a known entity keyword
  "Individual" — everything else
"""

ENTITY_KEYWORDS = {
    "llc", "inc", "corp", "trust", "l.p.", "lp", "ltd", "holdings",
    "properties", "realty", "investments", "investment", "group",
    "ventures", "venture", "partners", "partnership", "fund",
    "capital", "enterprises", "enterprise", "management", "mgmt",
    "acquisitions", "acquisition", "solutions", "services",
}


def detect_entity_type(buyer_name: str) -> str:
    """Return "LLC" if the name looks like a business entity, else "Individual"."""
    name_lower = buyer_name.lower()
    for kw in ENTITY_KEYWORDS:
        if kw in name_lower:
            return "LLC"
    return "Individual"


def score_priority(record: dict) -> str:
    """
    Return "Hot", "Warm", or "Standard" based on buyer history and entity type.
    Expects record to have times_bought_90d, purchase_price, and buyer_name.
    """
    times = record.get("times_bought_90d", 0)
    price = record.get("purchase_price", 0.0)
    entity_type = record.get("entity_type", detect_entity_type(record.get("buyer_name", "")))

    if times >= 2:
        return "Hot"
    if entity_type == "LLC" and price > 150_000:
        return "Warm"
    return "Standard"


def enrich_with_priority(records: list[dict]) -> list[dict]:
    """Add entity_type and priority columns to every record."""
    for r in records:
        r["entity_type"] = detect_entity_type(r.get("buyer_name", ""))
        r["priority"] = score_priority(r)
    return records
