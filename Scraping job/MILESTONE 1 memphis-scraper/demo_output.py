"""
Demo: builds one example record for each of the three output sheets
(Verified Leads, Needs Review, Discarded) using real lookups against the
live Shelby County Assessor portal, then writes them to Excel so the
3-sheet format can be inspected.

Run:
    python demo_output.py
Output:
    output/demo_verified_leads.xlsx
"""

import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

from src.assessor import verify_ownership
from src.output import write_output

OUTPUT_FILE = "output/demo_verified_leads.xlsx"

# (name, mdn_address, record_type, docket_number) — real names/addresses
# pulled from a real run's Tax Lien & Release records
DEMO_SOURCE = [
    ("Erica M Franklin", "1947 Glory Cir, Memphis Tn 38114",      "Tax Lien",         "26046959"),
    ("Hazel Thompson",   "148 State St Fl 6, Boston, MA 02109",   "Tax Lien Release", "26047565"),
    ("Geyer Fire Protection Llc Rv", "6008 Corporate Way, Indianapolis, IN 46278", "Tax Lien Release", "26047566"),
]

records = []

for name, mdn_address, record_type, docket in DEMO_SOURCE:
    print(f"\nLooking up: {name}")
    result = verify_ownership(name, mdn_address)

    record = {
        "filing_date":    "2026-06-08",
        "record_type":    record_type,
        "primary_name":   name,
        "secondary_name": "",
        "docket_number":  docket,
        "debt_amount":    "",
    }

    if result is None:
        record["verified_address"]   = ""
        record["unverified_address"] = ""
        record["parcel_id"]          = ""
        record["status"]             = "Discarded"
        record["discard_reason"]     = "No property found under name"
        print("  DISCARDED — no property found under name")
    elif result.get("status") == "Assessor Unavailable":
        record["verified_address"]   = ""
        record["unverified_address"] = ""
        record["parcel_id"]          = ""
        record["status"]             = "Assessor Unavailable"
        record["discard_reason"]     = "Assessor portal unreachable"
        print("  ASSESSOR UNAVAILABLE")
    elif result["status"] == "Needs Review":
        record["verified_address"]   = result["verified_address"]
        record["unverified_address"] = result["unverified_address"]
        record["parcel_id"]          = result["parcel_id"]
        record["status"]             = "Needs Review"
        record["discard_reason"]     = "Tax lien address did not match Assessor record(s) for this name"
        print(f"  NEEDS REVIEW — candidates={result['verified_address']}")
    else:
        record["verified_address"]   = result["verified_address"]
        record["unverified_address"] = result["unverified_address"]
        record["parcel_id"]          = result["parcel_id"]
        record["status"]             = "Verified"
        record["discard_reason"]     = ""
        print(f"  VERIFIED — {result['verified_address']}")

    records.append(record)

write_output(records, OUTPUT_FILE)
print(f"\nDone. Open: {OUTPUT_FILE}")
