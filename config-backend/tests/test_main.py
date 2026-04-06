"""
Tests for the config-backend FastAPI app.

Run from config-backend/:
    GITHUB_TOKEN=fake REPO_OWNER=fake REPO_NAME=fake pytest -v
"""

import os

import httpx
import respx
from httpx import ASGITransport, AsyncClient
from nacl.encoding import Base64Encoder
from nacl.public import PrivateKey

# ── Env vars must exist before importing main ─────────────────────────────────
os.environ.setdefault("GITHUB_TOKEN", "fake-token")
os.environ.setdefault("REPO_OWNER",   "fake-owner")
os.environ.setdefault("REPO_NAME",    "fake-repo")

from main import app, REPO_OWNER, REPO_NAME  # noqa: E402

# ── Shared constants ──────────────────────────────────────────────────────────

# Real Curve25519 keypair so PyNaCl encryption inside the app actually works.
_private_key    = PrivateKey.generate()
_public_key_b64 = _private_key.public_key.encode(encoder=Base64Encoder).decode()
MOCK_KEY_ID     = "mock-key-id-abc123"

GITHUB_BASE    = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}"
PUBLIC_KEY_URL = f"{GITHUB_BASE}/actions/secrets/public-key"
SECRET_NAMES   = ["EMAIL_SENDER", "EMAIL_RECIPIENT", "EMAIL_PASSWORD", "MDN_USERNAME", "MDN_PASSWORD"]

VALID_FORM = {
    "email":        "kash@example.com",
    "app_password": "abcd efgh ijkl mnop",
    "mdn_username": "kashuser",
    "mdn_password": "kashpass123",
}


def _register_github_ok(mock: respx.MockRouter) -> dict:
    """Register all GitHub API mocks for a successful run.

    Returns a dict of secret_name → Route so callers can assert .called on each.
    """
    mock.get(PUBLIC_KEY_URL).mock(
        return_value=httpx.Response(200, json={"key_id": MOCK_KEY_ID, "key": _public_key_b64})
    )
    routes = {}
    for name in SECRET_NAMES:
        routes[name] = mock.put(f"{GITHUB_BASE}/actions/secrets/{name}").mock(
            return_value=httpx.Response(204)
        )
    return routes


# ── Tests ─────────────────────────────────────────────────────────────────────

async def test_form_loads():
    """GET / returns 200 with HTML content containing a <form> element."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert 'id="save-btn"' in r.text


async def test_save_sets_all_five_secrets():
    """POST /save calls GitHub API for every expected secret name."""
    with respx.mock as mock:
        routes = _register_github_ok(mock)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/save", data=VALID_FORM)

        assert r.status_code == 200
        assert r.json() == {"status": "ok"}
        for name in SECRET_NAMES:
            assert routes[name].called, f"Expected PUT for {name} but it was never called"


async def test_save_email_used_as_both_sender_and_recipient():
    """EMAIL_SENDER and EMAIL_RECIPIENT are both set to the submitted email."""
    with respx.mock as mock:
        routes = _register_github_ok(mock)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/save", data={**VALID_FORM, "email": "kash@example.com"})

        assert r.status_code == 200
        assert routes["EMAIL_SENDER"].called,    "EMAIL_SENDER was not set"
        assert routes["EMAIL_RECIPIENT"].called, "EMAIL_RECIPIENT was not set"


async def test_save_github_api_error():
    """When GitHub returns 401, the endpoint returns 502 with an error message."""
    with respx.mock as mock:
        mock.get(PUBLIC_KEY_URL).mock(
            return_value=httpx.Response(401, json={"message": "Bad credentials"})
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/save", data=VALID_FORM)

    assert r.status_code == 502
    body = r.json()
    assert body["status"] == "error"
    assert "401" in body["message"]


async def test_run_triggers_selected_workflows():
    """POST /run triggers workflow_dispatch for each selected scraper."""
    with respx.mock as mock:
        for wf in ["cash_buyer.yml", "memphis_scraper.yml"]:
            mock.post(
                f"{GITHUB_BASE}/actions/workflows/{wf}/dispatches",
                name=f"dispatch_{wf}",
            ).mock(return_value=httpx.Response(204))

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post(
                "/run",
                json={"scrapers": ["cash_buyer", "memphis"]},
            )

        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert set(body["triggered"]) == {"cash_buyer", "memphis"}
        assert mock.routes["dispatch_cash_buyer.yml"].called
        assert mock.routes["dispatch_memphis_scraper.yml"].called


async def test_status_returns_latest_run_for_each_workflow():
    """GET /status returns status/conclusion for both workflows."""
    with respx.mock as mock:
        for wf in ["cash_buyer.yml", "memphis_scraper.yml"]:
            mock.get(
                f"{GITHUB_BASE}/actions/workflows/{wf}/runs",
                name=f"runs_{wf}",
            ).mock(return_value=httpx.Response(200, json={
                "workflow_runs": [{
                    "status":     "completed",
                    "conclusion": "success",
                    "created_at": "2026-07-05T10:00:00Z",
                    "html_url":   "https://github.com/test/test/actions/runs/123",
                }]
            }))

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/status")

        assert r.status_code == 200
        body = r.json()
        assert body["cash_buyer"]["status"] == "completed"
        assert body["cash_buyer"]["conclusion"] == "success"
        assert body["memphis"]["status"] == "completed"


async def test_save_missing_required_field():
    """POST /save without a required field returns 422 Unprocessable Entity."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/save", data={
            # email is intentionally missing
            "app_password": "abcd efgh ijkl mnop",
            "mdn_username": "kashuser",
            "mdn_password": "kashpass123",
        })
    assert r.status_code == 422
