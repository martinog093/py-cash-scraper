"""
Email delivery — sends the weekly CSV as an attachment.
Uses Gmail SMTP with an App Password (or any SMTP server).
"""

import logging
import os
import smtplib
from datetime import date
from email.message import EmailMessage
from dotenv import load_dotenv

logger = logging.getLogger(__name__)


def send_report(csv_path: str, record_count: int) -> None:
    load_dotenv()
    sender    = os.getenv("EMAIL_SENDER", "")
    password  = os.getenv("EMAIL_PASSWORD", "")
    recipient = os.getenv("EMAIL_RECIPIENT", "")

    if not all([sender, password, recipient]):
        logger.warning("Email credentials not configured — skipping email delivery")
        return

    msg = EmailMessage()
    msg["Subject"] = f"Cash Buyers Report — {date.today().isoformat()} ({record_count} leads)"
    msg["From"]    = sender
    msg["To"]      = recipient
    msg.set_content(
        f"Attached is your weekly cash buyer report for {date.today().isoformat()}.\n\n"
        f"Total confirmed cash sales: {record_count}\n\n"
        "Hot leads (2+ purchases in 90 days) are sorted to the top.\n"
    )

    xlsx_path = csv_path.replace(".csv", ".xlsx")
    if os.path.exists(xlsx_path):
        with open(xlsx_path, "rb") as f:
            msg.add_attachment(
                f.read(),
                maintype="application",
                subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                filename=os.path.basename(xlsx_path),
            )
    else:
        with open(csv_path, "rb") as f:
            msg.add_attachment(
                f.read(),
                maintype="text",
                subtype="csv",
                filename=os.path.basename(csv_path),
            )

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
            smtp.starttls()
            smtp.login(sender, password)
            smtp.send_message(msg)
        logger.info("Report emailed to %s", recipient)
    except Exception as e:
        logger.error("Email delivery failed: %s", e)
