"""
Email delivery — sends Memphis Daily News leads Excel files as attachments.
"""

import logging
import os
import smtplib
from datetime import date
from email.message import EmailMessage

logger = logging.getLogger(__name__)

OUTPUT_DIR = "output"
EXPECTED_FILES = ["tax_lien.xlsx", "probate.xlsx", "divorce.xlsx"]


def send_report() -> None:
    sender    = os.getenv("EMAIL_SENDER", "")
    password  = os.getenv("EMAIL_PASSWORD", "")
    recipient = os.getenv("EMAIL_RECIPIENT", "")

    if not all([sender, password, recipient]):
        logger.warning("Email credentials not configured — skipping email delivery")
        return

    attachments = [
        f for f in EXPECTED_FILES
        if os.path.exists(os.path.join(OUTPUT_DIR, f))
    ]

    if not attachments:
        logger.warning("No output files found in %s — skipping email delivery", OUTPUT_DIR)
        return

    today = date.today().isoformat()
    msg = EmailMessage()
    msg["Subject"] = f"Memphis Daily News Leads — {today} (Tax Liens, Probate, Divorce)"
    msg["From"]    = sender
    msg["To"]      = recipient
    msg.set_content(
        f"Attached are your Memphis Daily News leads for {today}.\n\n"
        "Files attached:\n" +
        "".join(f"  • {f}\n" for f in attachments) +
        "\nDiscarded records (no property match) are on the Discarded sheet in each file.\n"
    )

    for filename in attachments:
        filepath = os.path.join(OUTPUT_DIR, filename)
        with open(filepath, "rb") as f:
            msg.add_attachment(
                f.read(),
                maintype="application",
                subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                filename=filename,
            )
        logger.info("Attached: %s", filename)

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
            smtp.starttls()
            smtp.login(sender, password)
            smtp.send_message(msg)
        logger.info("Report emailed to %s (%d file(s) attached)", recipient, len(attachments))
    except Exception as e:
        logger.error("Email delivery failed: %s", e)
