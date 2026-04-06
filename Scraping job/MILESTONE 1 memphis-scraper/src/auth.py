import os
import logging
from dotenv import load_dotenv
from playwright.sync_api import Page, BrowserContext, TimeoutError as PlaywrightTimeoutError

logger = logging.getLogger(__name__)

LOGIN_URL = "https://www.memphisdailynews.com/Login.aspx"

# Actual field names confirmed from live page inspection
USERNAME_SELECTOR = 'input[name="ctl00$ContentPane$LoginUserTextBox"]'
PASSWORD_SELECTOR = 'input[name="ctl00$ContentPane$LoginPassTextBox"]'
SUBMIT_SELECTOR   = 'input[name="ctl00$ContentPane$LoginButton"]'
REMEMBER_SELECTOR = 'input[name="ctl00$ContentPane$RememberCheckBox"]'

LOGOUT_SELECTOR = 'a:has-text("Logout"), a:has-text("Log Out"), a:has-text("Sign Out")'


def login(context: BrowserContext) -> Page:
    load_dotenv()
    username = os.getenv("MDN_USERNAME")
    password = os.getenv("MDN_PASSWORD")

    if not username or not password:
        raise SystemExit(
            "ERROR: Credentials not found. Please create a .env file with MDN_USERNAME and MDN_PASSWORD."
        )

    page = context.new_page()

    try:
        logger.info("Navigating to login page...")
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)

        # Wait for the login form to be ready
        page.wait_for_selector(USERNAME_SELECTOR, timeout=15000)

        page.fill(USERNAME_SELECTOR, username)
        page.fill(PASSWORD_SELECTOR, password)

        remember = page.query_selector(REMEMBER_SELECTOR)
        if remember:
            remember.check()

        logger.info("Submitting login form...")
        page.click(SUBMIT_SELECTOR)
        page.wait_for_load_state("domcontentloaded", timeout=30000)

    except PlaywrightTimeoutError as e:
        raise RuntimeError(
            f"Login page timed out — the site may be down or the login form has changed.\nDetail: {e}"
        ) from None
    except Exception as e:
        raise RuntimeError(f"Unexpected error during login: {e}") from None

    # Verify login succeeded — login form should no longer be present
    if page.query_selector(SUBMIT_SELECTOR):
        raise RuntimeError(
            "Login failed — credentials rejected. Check MDN_USERNAME and MDN_PASSWORD in your .env file."
        )

    logger.info("Login successful.")
    return page


def logout(page: Page) -> None:
    """
    Log out of Memphis Daily News so the server-side session ends
    immediately. Without this, the site still considers the account
    "logged in" until the session times out on its own, and rejects the
    next run's login attempt with "previous session not ended".

    Best-effort: logs a warning and returns rather than raising, since by
    the time this runs the output file has already been written and a
    logout failure shouldn't fail the whole run.
    """
    try:
        page.on("dialog", lambda dialog: dialog.accept())

        logout_link = page.query_selector(LOGOUT_SELECTOR)
        if not logout_link:
            logger.warning("Logout link not found on page — session may remain active until it times out.")
            return

        logger.info("Logging out...")
        logout_link.click()

        # The "Log Out" link may be replaced via an AJAX partial postback
        # (UpdatePanel) rather than a full page navigation, so wait for the
        # link itself to disappear rather than for a page load event or for
        # Login.aspx's form fields (which logout doesn't navigate back to).
        page.wait_for_selector(LOGOUT_SELECTOR, state="detached", timeout=15000)
        logger.info("Logout successful.")

    except PlaywrightTimeoutError:
        logger.warning("Logout link clicked, but 'Log Out' link is still present — session may still be active.")
    except Exception as e:
        logger.warning("Logout failed: %s", e)
