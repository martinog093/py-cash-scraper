"""
Email delivery — sends the Memphis Daily News verified leads Excel as an attachment.
"""

import logging
import os
import smtplib
from datetime import date
from email.message import EmailMessage

logger = logging.getLogger(__name__)

XLSX_PATH = "output/verified_leads.xlsx"


def send_report() -> None:
    sender    = os.getenv("EMAIL_SENDER", "")
    password  = os.getenv("EMAIL_PASSWORD", "")
    recipient = os.getenv("EMAIL_RECIPIENT", "")

    if not all([sender, password, recipient]):
        logger.warning("Email credentials not configured — skipping email delivery")
        return

    if not os.path.exists(XLSX_PATH):
        logger.warning("Output file not found at %s — skipping email delivery", XLSX_PATH)
        return

    msg = EmailMessage()
    msg["Subject"] = f"Memphis Daily News Leads — {date.today().isoformat()} (Tax Liens, Probate, Divorce)"
    msg["From"]    = sender
    msg["To"]      = recipient
    msg.set_content(
        f"Attached is your Memphis Daily News leads report for {date.today().isoformat()}.\n\n"
        "The spreadsheet contains three categories:\n"
        "  • Tax Liens — verified property ownership via Shelby County Assessor\n"
        "  • Probate — deceased owner with assessor-confirmed property\n"
        "  • Divorce — plaintiff/defendant with assessor-confirmed property\n\n"
        "Discarded records (no property match found) are on the Discarded sheet.\n"
    )

    with open(XLSX_PATH, "rb") as f:
        msg.add_attachment(
            f.read(),
            maintype="application",
            subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=f"memphis_leads_{date.today().isoformat()}.xlsx",
        )

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
            smtp.starttls()
            smtp.login(sender, password)
            smtp.send_message(msg)
        logger.info("Report emailed to %s", recipient)
    except Exception as e:
        logger.error("Email delivery failed: %s", e)
