"""The demo door: /auth/demo signs in exactly one pre-seeded client
user, exists only on the client plane and only when configured, and can
never reach staff — no matter what is configured or supplied."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from starlette.applications import Starlette

from becca.db.session import SessionFactory
from becca.web import app as app_module
from tests.web.conftest import STAFF_EMAIL, csrf_from, sign_in

DEMO_EMAIL = "demo@becca.live"
DEMO_CODE = "sesame-open-up"


@pytest.fixture
def demo_app(monkeypatch: pytest.MonkeyPatch) -> Starlette:
    # The shared conftest fixture, plus the demo door configured.
    monkeypatch.setenv("ENVIRONMENT", "dev")
    monkeypatch.setenv("DATABASE_POOL", "null")
    monkeypatch.setenv("TELNYX_MODE", "fake")
    monkeypatch.setenv("TELNYX_API_KEY", "")
    monkeypatch.setenv("TELNYX_FROM_NUMBER", "+2340000000001")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("BECCA_STAFF_EMAILS", STAFF_EMAIL)
    monkeypatch.setenv("INLINE_WORKER", "false")
    monkeypatch.setenv("DEMO_ACCESS_CODE", DEMO_CODE)
    monkeypatch.setenv("DEMO_USER_EMAIL", DEMO_EMAIL)
    return app_module.create_app()


@pytest.fixture
def demo_console(demo_app: Starlette) -> Iterator[TestClient]:
    with TestClient(demo_app, base_url="http://console.localtest.me") as client:
        yield client


@pytest.fixture
def demo_client_plane(demo_app: Starlette) -> Iterator[TestClient]:
    with TestClient(demo_app, base_url="http://app.localtest.me") as client:
        yield client


def _seed_demo_user(console: TestClient) -> None:
    """The demo account exists like any other: staff created it."""
    sign_in(console, STAFF_EMAIL)
    token = csrf_from(console.get("/clients/new").text)
    console.post(
        "/clients",
        data={
            "csrf_token": token,
            "name": "Becca Demo",
            "rate_per_min": "0.30",
            "owner_email": DEMO_EMAIL,
        },
    )


def test_route_absent_when_unconfigured(db: SessionFactory, client_plane: TestClient) -> None:
    response = client_plane.get(f"/auth/demo?code={DEMO_CODE}", follow_redirects=False)
    assert response.status_code == 404


def test_route_absent_on_console_plane(db: SessionFactory, demo_console: TestClient) -> None:
    response = demo_console.get(f"/auth/demo?code={DEMO_CODE}", follow_redirects=False)
    assert response.status_code == 404


def test_wrong_code_is_refused(db: SessionFactory, demo_client_plane: TestClient) -> None:
    response = demo_client_plane.get("/auth/demo?code=wrong", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/auth/refused"
    assert "becca_session" not in response.cookies


def test_unseeded_demo_user_is_refused(db: SessionFactory, demo_client_plane: TestClient) -> None:
    response = demo_client_plane.get(f"/auth/demo?code={DEMO_CODE}", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/auth/refused"


def test_right_code_lands_demo_user_on_client_plane(
    db: SessionFactory, demo_console: TestClient, demo_client_plane: TestClient
) -> None:
    _seed_demo_user(demo_console)
    response = demo_client_plane.get(f"/auth/demo?code={DEMO_CODE}", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "http://app.localtest.me/"
    assert demo_client_plane.get("/").status_code == 200


async def test_staff_email_cannot_be_reached(
    db: SessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even configured with a staff email, the demo door refuses — it
    looks only at app_user, so the staff-materialisation branch of
    resolve_membership can never fire through it."""
    monkeypatch.setenv("ENVIRONMENT", "dev")
    monkeypatch.setenv("DATABASE_POOL", "null")
    monkeypatch.setenv("TELNYX_MODE", "fake")
    monkeypatch.setenv("TELNYX_API_KEY", "")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("BECCA_STAFF_EMAILS", STAFF_EMAIL)
    monkeypatch.setenv("INLINE_WORKER", "false")
    monkeypatch.setenv("DEMO_ACCESS_CODE", DEMO_CODE)
    monkeypatch.setenv("DEMO_USER_EMAIL", STAFF_EMAIL)
    app = app_module.create_app()
    with TestClient(app, base_url="http://app.localtest.me") as client:
        response = client.get(f"/auth/demo?code={DEMO_CODE}", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/auth/refused"
    async with db.console_session() as s:
        staff_rows = (
            await s.execute(
                text("SELECT count(*) FROM becca_staff WHERE google_email = :e"),
                {"e": STAFF_EMAIL},
            )
        ).scalar_one()
    assert staff_rows == 0, "demo door materialised a staff row"


def test_inline_worker_lifespan_starts_and_stops(
    db: SessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """INLINE_WORKER=true runs the worker loop for the life of the app
    (fake gateway here) and shuts down cleanly with the TestClient."""
    monkeypatch.setenv("ENVIRONMENT", "dev")
    monkeypatch.setenv("DATABASE_POOL", "null")
    monkeypatch.setenv("TELNYX_MODE", "fake")
    monkeypatch.setenv("TELNYX_API_KEY", "")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("BECCA_STAFF_EMAILS", STAFF_EMAIL)
    monkeypatch.setenv("DEMO_ACCESS_CODE", "")
    monkeypatch.setenv("DEMO_USER_EMAIL", "")
    monkeypatch.setenv("INLINE_WORKER", "true")
    app = app_module.create_app()
    with TestClient(app, base_url="http://app.localtest.me") as client:
        response = client.get("/auth/signin")
        assert response.status_code == 200
    # Exiting the context manager runs the lifespan shutdown; a hung or
    # crashed cancellation would raise here.
