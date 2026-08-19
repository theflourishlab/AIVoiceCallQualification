"""Staff signing in on the client plane must land on the console, not
loop through sign-in (regression: looked like a failed login)."""

from fastapi.testclient import TestClient

from becca.db.session import SessionFactory
from tests.web.conftest import STAFF_EMAIL, sign_in


def test_staff_without_entered_account_is_sent_to_console(
    db: SessionFactory, client_plane: TestClient
) -> None:
    sign_in(client_plane, STAFF_EMAIL)
    response = client_plane.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("http://console.localtest.me")
