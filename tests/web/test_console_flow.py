"""Phase 6 console flows: landing columns + balance, numbers & capacity,
staff management, and the audit trail (FR-CONSOLE-1/3/5/8, FR-AUTH-3)."""

from fastapi.testclient import TestClient
from sqlalchemy import text

from becca.db.session import SessionFactory
from tests.web.conftest import STAFF_EMAIL, csrf_from, sign_in


def _create_client_account(console: TestClient, name: str = "Sylvastar") -> None:
    page = console.get("/clients/new")
    response = console.post(
        "/clients",
        data={
            "csrf_token": csrf_from(page.text),
            "name": name,
            "rate_per_min": "0.30",
            "owner_email": "engineer@becca.live",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text


async def test_landing_shows_columns_and_balance(console: TestClient, db: SessionFactory) -> None:
    sign_in(console, STAFF_EMAIL)
    _create_client_account(console)
    page = console.get("/")
    assert page.status_code == 200
    # FR-CONSOLE-1 columns are present.
    for column in ["People", "Channels", "Number", "Calls MTD", "Billed MTD", "Status"]:
        assert column in page.text
    assert "not assigned" in page.text  # no number yet
    # FR-NOTIFY-2A: the fake gateway's balance renders persistently.
    assert "$42.50" in page.text
    # FR-CONSOLE-4: the onboarding checklist appears for the new account.
    assert "Setting up Sylvastar" in page.text


async def test_numbers_screen_syncs_assigns_and_allocates(
    console: TestClient, db: SessionFactory
) -> None:
    sign_in(console, STAFF_EMAIL)
    _create_client_account(console)
    async with db.console_session() as s:
        client_id = (await s.execute(text("SELECT id FROM client_account"))).scalar_one()

    page = console.get("/numbers")
    assert page.status_code == 200
    # Inventory synced from the fake gateway on load (FR-CONSOLE-5).
    assert "+2342093940544" in page.text
    csrf = csrf_from(page.text)

    async with db.console_session() as s:
        number_id = (
            await s.execute(text("SELECT id FROM phone_number WHERE phone_e164 = '+2342093940544'"))
        ).scalar_one()

    response = console.post(
        f"/numbers/{number_id}/assign",
        data={"csrf_token": csrf, "client_id": str(client_id)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "done=assigned" in response.headers["location"]

    response = console.post(
        f"/allocation/{client_id}",
        data={"csrf_token": csrf, "channels": "4"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "done=allocated" in response.headers["location"]

    # Oversubscription bounces with the ceiling error (FR-CONSOLE-3).
    response = console.post(
        f"/allocation/{client_id}",
        data={"csrf_token": csrf, "channels": "11"},
        follow_redirects=False,
    )
    assert "error=ceiling" in response.headers["location"]

    # The landing table now shows the assignment and the split.
    page = console.get("/")
    assert "+2342093940544" in page.text
    assert "4 / 10" in page.text

    # Every mutation wrote an audit row (FR-CONSOLE-8).
    async with db.console_session() as s:
        actions = (
            (await s.execute(text("SELECT action FROM audit_log ORDER BY id"))).scalars().all()
        )
    assert "assigned_number" in actions
    assert "set_channel_allocation" in actions


async def test_order_number_appends_to_inventory(console: TestClient, db: SessionFactory) -> None:
    sign_in(console, STAFF_EMAIL)
    page = console.get("/numbers")
    response = console.post(
        "/numbers/order",
        data={"csrf_token": csrf_from(page.text)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "done=ordered" in response.headers["location"]
    async with db.console_session() as s:
        count = (await s.execute(text("SELECT count(*) FROM phone_number"))).scalar_one()
    assert count == 3  # the fake account's two + the ordered one
    async with db.console_session() as s:
        actions = (await s.execute(text("SELECT action FROM audit_log"))).scalars().all()
    assert "ordered_number" in actions


async def test_staff_management(console: TestClient, db: SessionFactory) -> None:
    """FR-AUTH-3: add by Google email, no invitation; removal guarded."""
    sign_in(console, STAFF_EMAIL)
    page = console.get("/staff")
    assert page.status_code == 200
    assert STAFF_EMAIL.upper() in page.text
    csrf = csrf_from(page.text)

    response = console.post(
        "/staff",
        data={"csrf_token": csrf, "email": "chinedu@becca.live"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    # The new address signs in and it just works (FR-AUTH-3).
    fresh = console.get("/auth/dev?email=chinedu@becca.live", follow_redirects=False)
    assert fresh.status_code == 303
    assert fresh.headers["location"] == "http://console.localtest.me/"

    async with db.console_session() as s:
        me = (
            await s.execute(
                text("SELECT id FROM becca_staff WHERE google_email = :e"),
                {"e": STAFF_EMAIL},
            )
        ).scalar_one()
        other = (
            await s.execute(
                text("SELECT id FROM becca_staff WHERE google_email = 'chinedu@becca.live'")
            )
        ).scalar_one()

    # Self-removal refused: no locking yourself out. (Signing in again
    # minted a fresh session, so fetch its CSRF token.)
    sign_in(console, STAFF_EMAIL)
    csrf = csrf_from(console.get("/staff").text)
    response = console.post(
        f"/staff/{me}/remove", data={"csrf_token": csrf}, follow_redirects=False
    )
    assert "error=self" in response.headers["location"]

    # Removing the other is immediate — the next request refuses them.
    response = console.post(
        f"/staff/{other}/remove", data={"csrf_token": csrf}, follow_redirects=False
    )
    assert response.status_code == 303
    async with db.console_session() as s:
        count = (await s.execute(text("SELECT count(*) FROM becca_staff"))).scalar_one()
    assert count == 1
    async with db.console_session() as s:
        actions = (await s.execute(text("SELECT action FROM audit_log"))).scalars().all()
    assert "added_staff" in actions
    assert "removed_staff" in actions


async def test_seeded_staff_removal_refused(console: TestClient, db: SessionFactory) -> None:
    """FR-AUTH-2 re-seeds config addresses on sign-in, so deleting the
    row would silently undo itself — refused with the reason."""
    sign_in(console, STAFF_EMAIL)  # materialises the seeded staff row
    async with db.console_session() as s:
        me = (
            await s.execute(
                text("SELECT id FROM becca_staff WHERE google_email = :e"),
                {"e": STAFF_EMAIL},
            )
        ).scalar_one()
        # A second staff member does the removing, so the self-guard
        # is not what fires.
        await s.execute(
            text("INSERT INTO becca_staff (google_email) VALUES ('chinedu@becca.live')")
        )
    sign_in(console, "chinedu@becca.live")
    response = console.post(
        f"/staff/{me}/remove",
        data={"csrf_token": csrf_from(console.get("/staff").text)},
        follow_redirects=False,
    )
    assert "error=seeded" in response.headers["location"]
    async with db.console_session() as s:
        count = (await s.execute(text("SELECT count(*) FROM becca_staff"))).scalar_one()
    assert count == 2


async def test_console_screens_refuse_client_users(
    console: TestClient, client_plane: TestClient, db: SessionFactory
) -> None:
    """FR-ARCH-3: the console reveals nothing to a client session."""
    sign_in(console, STAFF_EMAIL)
    _create_client_account(console)
    sign_in(client_plane, "engineer@becca.live")
    for path in ["/numbers", "/staff"]:
        response = client_plane.get(f"http://console.localtest.me{path}", follow_redirects=False)
        assert response.status_code == 403, path


async def test_client_people_management(console: TestClient, db: SessionFactory) -> None:
    """FR-AUTH-4/5: the People count opens a real list; staff add and
    remove client users; the account never goes ownerless."""
    sign_in(console, STAFF_EMAIL)
    _create_client_account(console)  # creates the owner engineer@becca.live
    async with db.console_session() as s:
        client_id = (await s.execute(text("SELECT id FROM client_account"))).scalar_one()
        owner_id = (await s.execute(text("SELECT id FROM app_user"))).scalar_one()

    page = console.get(f"/clients/{client_id}/people")
    assert page.status_code == 200
    assert "ENGINEER@BECCA.LIVE" in page.text
    assert "NEVER SIGNED IN" in page.text
    csrf = csrf_from(page.text)

    # The landing People count links here.
    landing = console.get("/")
    assert f"/clients/{client_id}/people" in landing.text

    # Add a member; they sign in and it works (FR-AUTH-5).
    response = console.post(
        f"/clients/{client_id}/people",
        data={"csrf_token": csrf, "email": "tunde.balogun@gmail.com", "role": "member"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    fresh = console.get("/auth/dev?email=tunde.balogun@gmail.com", follow_redirects=False)
    assert fresh.headers["location"] == "http://app.localtest.me/"
    sign_in(console, STAFF_EMAIL)
    csrf = csrf_from(console.get(f"/clients/{client_id}/people").text)

    # A duplicate email bounces — one address, one membership.
    response = console.post(
        f"/clients/{client_id}/people",
        data={"csrf_token": csrf, "email": "tunde.balogun@gmail.com", "role": "member"},
        follow_redirects=False,
    )
    assert "error=exists" in response.headers["location"]

    # The only owner cannot be removed (FR-AUTH-7 would strand launch).
    response = console.post(
        f"/clients/{client_id}/people/{owner_id}/remove",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert "error=last_owner" in response.headers["location"]

    # A member removes fine, and the actions are audited.
    async with db.console_session() as s:
        member_id = (
            await s.execute(
                text("SELECT id FROM app_user WHERE google_email = 'tunde.balogun@gmail.com'")
            )
        ).scalar_one()
    response = console.post(
        f"/clients/{client_id}/people/{member_id}/remove",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303
    async with db.console_session() as s:
        count = (await s.execute(text("SELECT count(*) FROM app_user"))).scalar_one()
        actions = (await s.execute(text("SELECT action FROM audit_log"))).scalars().all()
    assert count == 1
    assert "added_client_user" in actions
    assert "removed_client_user" in actions


async def test_audit_feed_renders_on_staff_screen(console: TestClient, db: SessionFactory) -> None:
    sign_in(console, STAFF_EMAIL)
    _create_client_account(console)
    page = console.get("/staff")
    assert "Recent console activity" in page.text
    assert "created client account" in page.text
