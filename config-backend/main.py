"""
Config backend for Kash's scraper setup.

Endpoints:
  GET  /        — serves the HTML config form
  POST /save    — encrypts and pushes 5 GitHub Secrets to the repo
  POST /run     — triggers one or both GitHub Actions workflows
  GET  /status  — returns the latest run status for each workflow

Required environment variables (set in Render dashboard):
  GITHUB_TOKEN  — Personal Access Token with 'repo' scope
  REPO_OWNER    — GitHub username / org (e.g. "RoadtoFire")
  REPO_NAME     — Repository name (e.g. "kash-scraper")
  REPO_BRANCH   — Branch to dispatch workflows on (default: "main")
"""

import base64
import os
from pathlib import Path

import httpx
from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, JSONResponse
from nacl.encoding import Base64Encoder
from nacl.public import PublicKey, SealedBox
from pydantic import BaseModel

app = FastAPI()

GITHUB_TOKEN  = os.environ["GITHUB_TOKEN"]
REPO_OWNER    = os.environ["REPO_OWNER"]
REPO_NAME     = os.environ["REPO_NAME"]
REPO_BRANCH   = os.getenv("REPO_BRANCH", "main")

STATIC_DIR = Path(__file__).parent / "static"

GITHUB_HEADERS = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

GITHUB_BASE = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}"

WORKFLOW_FILES = {
    "cash_buyer": "cash_buyer.yml",
    "memphis":    "memphis_scraper.yml",
}


@app.get("/", response_class=HTMLResponse)
async def form():
    return (STATIC_DIR / "index.html").read_text()


async def _set_variable(client: httpx.AsyncClient, name: str, value: str) -> None:
    """Create or update a GitHub Actions repository variable (readable, not encrypted).
    Variables API: PATCH to update existing, POST to create new.
    """
    patch = await client.patch(
        f"{GITHUB_BASE}/actions/variables/{name}",
        headers=GITHUB_HEADERS,
        json={"name": name, "value": value},
    )
    if patch.status_code == 204:
        return  # updated
    if patch.status_code == 404:
        post = await client.post(
            f"{GITHUB_BASE}/actions/variables",
            headers=GITHUB_HEADERS,
            json={"name": name, "value": value},
        )
        if post.status_code not in (201, 204):
            post.raise_for_status()
        return
    patch.raise_for_status()


@app.get("/config")
async def get_config():
    """Return saved non-sensitive config (email + MDN username) from GitHub Variables."""
    result: dict[str, str] = {}
    try:
        async with httpx.AsyncClient() as client:
            for var in ("CONFIG_EMAIL", "CONFIG_MDN_USERNAME"):
                resp = await client.get(
                    f"{GITHUB_BASE}/actions/variables/{var}",
                    headers=GITHUB_HEADERS,
                )
                if resp.status_code == 200:
                    result[var] = resp.json().get("value", "")
    except Exception:
        pass  # best-effort — form just stays blank
    return JSONResponse(result)


@app.post("/save")
async def save(
    email: str        = Form(...),
    app_password: str = Form(...),
    mdn_username: str = Form(...),
    mdn_password: str = Form(...),
):
    """Encrypt and push all five GitHub Secrets; also save non-sensitive fields as Variables."""
    secrets = {
        "EMAIL_SENDER":    email,
        "EMAIL_RECIPIENT": email,   # Kash emails himself
        "EMAIL_PASSWORD":  app_password.replace(" ", ""),  # strip spaces — Gmail SMTP requires no spaces
        "MDN_USERNAME":    mdn_username,
        "MDN_PASSWORD":    mdn_password,
    }
    # Non-sensitive fields stored as readable Variables so the form can pre-populate on any device
    variables = {
        "CONFIG_EMAIL":        email,
        "CONFIG_MDN_USERNAME": mdn_username,
    }

    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{GITHUB_BASE}/actions/secrets/public-key",
                headers=GITHUB_HEADERS,
            )
            r.raise_for_status()
            key_data = r.json()

            pub_key = PublicKey(key_data["key"].encode(), encoder=Base64Encoder)
            box = SealedBox(pub_key)

            for name, value in secrets.items():
                encrypted = base64.b64encode(box.encrypt(value.encode())).decode()
                resp = await client.put(
                    f"{GITHUB_BASE}/actions/secrets/{name}",
                    headers=GITHUB_HEADERS,
                    json={"encrypted_value": encrypted, "key_id": key_data["key_id"]},
                )
                resp.raise_for_status()

            for name, value in variables.items():
                await _set_variable(client, name, value)

    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        return JSONResponse(
            {"status": "error", "message": f"GitHub API returned {status}. Check that your token has repo access."},
            status_code=502,
        )
    except Exception as exc:
        return JSONResponse(
            {"status": "error", "message": str(exc)},
            status_code=500,
        )

    return JSONResponse({"status": "ok"})


class RunRequest(BaseModel):
    scrapers: list[str]
    zips: list[str] | None = None                  # Shelby ZIP filter; None means all 41
    bergen_municipalities: list[str] | None = None  # Bergen municipality filter; None means all 39
    min_price: int | None = None                    # Minimum sale price in USD; None = default 50000
    start_date: str | None = None
    end_date: str | None = None
    days: int | None = None


@app.post("/run")
async def run_scrapers(req: RunRequest):
    """Trigger one or both GitHub Actions workflows via workflow_dispatch."""
    triggered = []
    errors    = []

    try:
        async with httpx.AsyncClient() as client:
            for scraper in req.scrapers:
                workflow_file = WORKFLOW_FILES.get(scraper)
                if not workflow_file:
                    errors.append(f"Unknown scraper: {scraper}")
                    continue

                dispatch_inputs: dict[str, str] = {}
                if scraper == "cash_buyer" and req.zips:
                    dispatch_inputs["shelby_zips"] = ",".join(req.zips)
                if scraper == "cash_buyer" and req.bergen_municipalities:
                    dispatch_inputs["bergen_municipalities"] = ",".join(req.bergen_municipalities)
                if scraper == "cash_buyer" and req.days:
                    dispatch_inputs["days"] = str(req.days)
                if scraper == "cash_buyer" and req.min_price:
                    dispatch_inputs["min_price"] = str(req.min_price)
                if scraper == "memphis" and req.start_date and req.end_date:
                    dispatch_inputs["start_date"] = req.start_date
                    dispatch_inputs["end_date"] = req.end_date

                resp = await client.post(
                    f"{GITHUB_BASE}/actions/workflows/{workflow_file}/dispatches",
                    headers=GITHUB_HEADERS,
                    json={"ref": REPO_BRANCH, "inputs": dispatch_inputs},
                )
                if resp.status_code == 204:
                    triggered.append(scraper)
                else:
                    errors.append(f"{scraper}: GitHub returned {resp.status_code}")

    except httpx.HTTPStatusError as exc:
        return JSONResponse(
            {"status": "error", "message": f"GitHub API error {exc.response.status_code}"},
            status_code=502,
        )
    except Exception as exc:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=500)

    return JSONResponse({"status": "ok", "triggered": triggered, "errors": errors})


@app.post("/test-email")
async def test_email():
    """Trigger the test_email.yml workflow to verify email credentials."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{GITHUB_BASE}/actions/workflows/test_email.yml/dispatches",
                headers=GITHUB_HEADERS,
                json={"ref": REPO_BRANCH, "inputs": {}},
            )
            if resp.status_code == 204:
                return JSONResponse({"status": "ok"})
            return JSONResponse(
                {"status": "error", "message": f"GitHub returned {resp.status_code}"},
                status_code=502,
            )
    except Exception as exc:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=500)


@app.get("/status")
async def get_status():
    """Return the latest run for each workflow (status, conclusion, when, link)."""
    result: dict[str, dict | None] = {}

    try:
        async with httpx.AsyncClient() as client:
            for key, workflow_file in WORKFLOW_FILES.items():
                resp = await client.get(
                    f"{GITHUB_BASE}/actions/workflows/{workflow_file}/runs",
                    headers=GITHUB_HEADERS,
                    params={"per_page": 1},
                )
                if resp.status_code != 200:
                    result[key] = None
                    continue

                runs = resp.json().get("workflow_runs", [])
                if not runs:
                    result[key] = None
                    continue

                run = runs[0]
                result[key] = {
                    "status":     run["status"],       # queued | in_progress | completed
                    "conclusion": run["conclusion"],    # success | failure | cancelled | null
                    "created_at": run["created_at"],
                    "url":        run["html_url"],
                }

    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)

    return JSONResponse(result)
