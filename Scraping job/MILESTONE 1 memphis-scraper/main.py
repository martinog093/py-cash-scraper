import sys
import time
from datetime import date

import tax_lien_scraper
import probate_scraper
import divorce_scraper

# Memphis Daily News rejects logins that follow too soon after a previous
# session's logout (observed as "credentials rejected" on the 2nd/3rd script
# in a chained run). Wait between scripts to avoid tripping this.
LOGIN_COOLDOWN_SECONDS = 5 * 60


def main():
    if len(sys.argv) >= 3:
        start = date.fromisoformat(sys.argv[1])
        end = date.fromisoformat(sys.argv[2])
    elif len(sys.argv) == 2:
        start = date.fromisoformat(sys.argv[1])
        end = start
    else:
        start = None
        end = None

    print("=== Running Tax Lien scraper ===")
    tax_lien_scraper.run(start, end)

    print(f"=== Waiting {LOGIN_COOLDOWN_SECONDS // 60} min before next login ===")
    time.sleep(LOGIN_COOLDOWN_SECONDS)

    print("=== Running Probate scraper ===")
    probate_scraper.run(start, end)

    print(f"=== Waiting {LOGIN_COOLDOWN_SECONDS // 60} min before next login ===")
    time.sleep(LOGIN_COOLDOWN_SECONDS)

    print("=== Running Divorce scraper ===")
    divorce_scraper.run(start, end)

    print("=== All done ===")

    print("=== Sending email report ===")
    try:
        from src.email_sender import send_report
        send_report()
    except Exception as e:
        print(f"Email delivery failed: {e}")


if __name__ == "__main__":
    main()
