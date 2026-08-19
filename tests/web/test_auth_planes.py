"""FR-AUTH-1 (no sign-up, refusal), FR-AUTH-2 (seeded staff),
FR-ARCH-3 (client session on console -> 403)."""

from fastapi.testclient import TestClient

from becca.db.session import SessionFactory
from tests.web.conftest import STAFF_EMAIL, csrf_from, sign_in


def test_unknown_email_is_refused(db: SessionFactory, console: TestClient) -> None:
    response = console.get("/auth/dev?email=stranger@example.com", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/auth/refused"


def test_seeded_staff_signs_in_and_sees_console(db: SessionFactory, console: TestClient) -> None:
    sign_in(console, STAFF_EMAIL)  # materialises the seeded staff row (FR-AUTH-2)
    page = console.get("/")
    assert page.status_code == 200
    assert "Clients" in page.text


def test_client_user_gets_403_on_console(
    db: SessionFactory, console: TestClient, client_plane: TestClient
) -> None:
    # Staff creates a client account with an owner (FR-AUTH-5).
    sign_in(console, STAFF_EMAIL)
    token = csrf_from(console.get("/clients/new").text)
    console.post(
        "/clients",
        data={
            "csrf_token": token,
            "name": "Sylvastar",
            "rate_per_min": "0.30",
            "owner_email": "sylvester@sylvastar.ng",
        },
    )
    # The owner signs in on the client plane…
    sign_in(client_plane, "sylvester@sylvastar.ng")
    assert client_plane.get("/").status_code == 200
    # …and the same client session presented to a console endpoint gets
    # 403 (FR-ARCH-3). A fresh console client carries only their cookie.
    from fastapi.testclient import TestClient as _TC

    with _TC(console.app, base_url="http://console.localtest.me") as their_session:
        sign_in(their_session, "sylvester@sylvastar.ng")
        assert their_session.get("/").status_code == 403


def test_unauthenticated_is_redirected_to_signin(
    db: SessionFactory, client_plane: TestClient
) -> None:
    response = client_plane.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/auth/signin"


def test_post_without_csrf_token_is_refused(db: SessionFactory, console: TestClient) -> None:
    sign_in(console, STAFF_EMAIL)
    response = console.post(
        "/clients",
        data={"name": "X", "rate_per_min": "0.30", "owner_email": "x@x.ng"},
    )
    assert response.status_code == 403
