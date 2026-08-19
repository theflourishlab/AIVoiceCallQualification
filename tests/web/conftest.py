import re
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette

from becca.web import app as app_module

STAFF_EMAIL = "flourish@becca.live"


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> Starlette:
    monkeypatch.setenv("ENVIRONMENT", "dev")
    monkeypatch.setenv("DATABASE_POOL", "null")  # one app, several test loops
    # The developer's .env may carry TELNYX_MODE=real — tests must NEVER
    # construct the real gateway, or a test could dial a real number.
    monkeypatch.setenv("TELNYX_MODE", "fake")
    monkeypatch.setenv("TELNYX_API_KEY", "")
    # Same principle for the caller-ID fallback: launch's preflight lets a
    # fake-mode run dial from it (launch.py's number_ok), so a developer
    # .env with it set passes while CI, with no .env, blocked every launch.
    monkeypatch.setenv("TELNYX_FROM_NUMBER", "+2340000000001")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")  # FakeGenerator
    monkeypatch.setenv("BECCA_STAFF_EMAILS", STAFF_EMAIL)
    return app_module.create_app()


@pytest.fixture
def console(app: Starlette) -> Iterator[TestClient]:
    with TestClient(app, base_url="http://console.localtest.me") as client:
        yield client


@pytest.fixture
def client_plane(app: Starlette) -> Iterator[TestClient]:
    with TestClient(app, base_url="http://app.localtest.me") as client:
        yield client


def sign_in(client: TestClient, email: str) -> None:
    response = client.get(f"/auth/dev?email={email}", follow_redirects=False)
    assert response.status_code == 303, response.text
    # Sign-in now lands each kind on its own plane's host (absolute URL).
    assert "/auth/refused" not in response.headers["location"], "membership refused"


def csrf_from(html: str) -> str:
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert m, "no CSRF token in page"
    return m.group(1)


def fund_wallet(console: TestClient, name: str = "Sylvastar", amount: float = 1000.0) -> None:
    """Top up a client's wallet through the real console credit route.
    Since the wallet, an unfunded client can't place a single call, so
    every flow that dials must fund first — like production."""
    page = console.get("/billing").text
    row = re.compile(
        r'cell-strong">' + re.escape(name) + r'</td>.*?action="/billing/credit/([0-9a-f-]+)"',
        re.S,
    ).search(page)
    assert row, f"no wallet row for {name} on the billing screen"
    response = console.post(
        f"/billing/credit/{row.group(1)}",
        data={"csrf_token": csrf_from(page), "amount_usd": str(amount), "note": "test seed"},
        follow_redirects=False,
    )
    assert "done=credited" in response.headers["location"], response.text
