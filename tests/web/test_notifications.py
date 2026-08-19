"""FR-NOTIFY-1/3: the panel, the unread count, per-user prefs — and
tenant isolation between audiences."""

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import text

from becca.db.session import SessionFactory
from becca.services import notify
from tests.web.conftest import STAFF_EMAIL, csrf_from, sign_in
from tests.web.test_console_flow import _create_client_account

OWNER = "engineer@becca.live"


async def _emit_for_client(db: SessionFactory, event: str = "run_finished") -> uuid.UUID:
    async with db.console_session() as s:
        client_id = (
            await s.execute(text("SELECT id FROM client_account ORDER BY created_at LIMIT 1"))
        ).scalar_one()
        await notify.emit(
            s,
            event=event,
            title=notify.CLIENT_EVENTS[event],
            body="Visit Qualifier has called everyone.",
            client_account_id=client_id,
        )
    return uuid.UUID(str(client_id))


async def test_client_panel_unread_and_mark_read(
    console: TestClient, client_plane: TestClient, db: SessionFactory
) -> None:
    sign_in(console, STAFF_EMAIL)
    _create_client_account(console)
    await _emit_for_client(db, "run_finished")
    await _emit_for_client(db, "spend_cap_reached")

    sign_in(client_plane, OWNER)
    assert client_plane.get("/notifications/unread").json() == {"count": 2}
    page = client_plane.get("/notifications")
    assert page.status_code == 200
    assert "results ready" in page.text
    assert "Spend cap reached" in page.text

    response = client_plane.post(
        "/notifications/read-all",
        data={"csrf_token": csrf_from(page.text)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert client_plane.get("/notifications/unread").json() == {"count": 0}
    # Read rows remain visible in the history, dimmed not deleted.
    assert "Spend cap reached" in client_plane.get("/notifications").text


async def test_read_state_is_per_reader(
    console: TestClient, client_plane: TestClient, db: SessionFactory
) -> None:
    """A second user of the same account keeps their own unread badge."""
    sign_in(console, STAFF_EMAIL)
    _create_client_account(console)
    async with db.console_session() as s:
        client_id = (await s.execute(text("SELECT id FROM client_account"))).scalar_one()
        await s.execute(
            text(
                "INSERT INTO app_user (client_account_id, google_email, role)"
                " VALUES (:cid, 'tunde@becca.live', 'member')"
            ),
            {"cid": str(client_id)},
        )
    await _emit_for_client(db)

    sign_in(client_plane, OWNER)
    page = client_plane.get("/notifications")
    client_plane.post(
        "/notifications/read-all",
        data={"csrf_token": csrf_from(page.text)},
        follow_redirects=False,
    )
    assert client_plane.get("/notifications/unread").json() == {"count": 0}

    sign_in(client_plane, "tunde@becca.live")
    assert client_plane.get("/notifications/unread").json() == {"count": 1}


async def test_pref_off_silences_badge_and_panel(
    console: TestClient, client_plane: TestClient, db: SessionFactory
) -> None:
    """FR-NOTIFY-3: per-user, per-event. Toggling off hides existing
    rows and the badge; toggling back restores them (nothing deleted)."""
    sign_in(console, STAFF_EMAIL)
    _create_client_account(console)
    await _emit_for_client(db, "import_blocked")

    sign_in(client_plane, OWNER)
    page = client_plane.get("/notifications")
    csrf = csrf_from(page.text)
    # Save prefs with import_blocked unchecked (all others on).
    on = {e: "on" for e in notify.CLIENT_EVENTS if e != "import_blocked"}
    client_plane.post(
        "/notifications/prefs", data={"csrf_token": csrf, **on}, follow_redirects=False
    )
    assert client_plane.get("/notifications/unread").json() == {"count": 0}
    # The item is hidden (its body vanishes); the pref checkbox label
    # legitimately still names the event.
    assert "Visit Qualifier has called everyone." not in client_plane.get("/notifications").text

    client_plane.post(
        "/notifications/prefs",
        data={"csrf_token": csrf, **on, "import_blocked": "on"},
        follow_redirects=False,
    )
    assert client_plane.get("/notifications/unread").json() == {"count": 1}


async def test_audiences_are_isolated(
    console: TestClient, client_plane: TestClient, db: SessionFactory
) -> None:
    """Staff rows never reach a client; one client's rows never reach
    another; the console panel shows only Becca-side events."""
    sign_in(console, STAFF_EMAIL)
    _create_client_account(console, name="Sylvastar")
    page = console.get("/clients/new")
    console.post(
        "/clients",
        data={
            "csrf_token": csrf_from(page.text),
            "name": "Lekki Gardens",
            "rate_per_min": "0.30",
            "owner_email": "lekki-owner@becca.live",
        },
        follow_redirects=False,
    )
    await _emit_for_client(db)  # Sylvastar (first created)
    async with db.console_session() as s:
        await notify.emit(
            s,
            event="balance_low",
            title=notify.STAFF_EVENTS["balance_low"],
            body="$8.40 left.",
        )

    sign_in(client_plane, "lekki-owner@becca.live")
    assert client_plane.get("/notifications/unread").json() == {"count": 0}
    text_ = client_plane.get("/notifications").text
    # Bodies are the leak test — pref labels legitimately name events.
    assert "Visit Qualifier has called everyone." not in text_
    assert "$8.40 left." not in text_

    sign_in(client_plane, OWNER)
    assert client_plane.get("/notifications/unread").json() == {"count": 1}

    assert console.get("/notifications/unread").json() == {"count": 1}
    console_text = console.get("/notifications").text
    assert "$8.40 left." in console_text
    assert "Visit Qualifier has called everyone." not in console_text


async def test_dedupe_window(db: SessionFactory, console: TestClient) -> None:
    sign_in(console, STAFF_EMAIL)
    async with db.console_session() as s:
        first = await notify.emit(
            s, event="balance_low", title="Telnyx balance low", dedupe_hours=24
        )
        second = await notify.emit(
            s, event="balance_low", title="Telnyx balance low", dedupe_hours=24
        )
        count = (await s.execute(text("SELECT count(*) FROM notification"))).scalar_one()
    assert first is True
    assert second is False
    assert int(count) == 1


async def test_import_with_undiallable_rows_notifies(
    console: TestClient, client_plane: TestClient, db: SessionFactory
) -> None:
    """The upload emitter fires when rows are excluded (FR-NOTIFY-2B)."""
    sign_in(console, STAFF_EMAIL)
    _create_client_account(console)
    async with db.console_session() as s:
        client_id = (await s.execute(text("SELECT id FROM client_account"))).scalar_one()
        agent_id = (
            await s.execute(
                text(
                    "INSERT INTO agent (client_account_id, name, status)"
                    " VALUES (:cid, 'Qualifier', 'tested') RETURNING id"
                ),
                {"cid": str(client_id)},
            )
        ).scalar_one()
        version_id = (
            await s.execute(
                text(
                    "INSERT INTO agent_version (agent_id, client_account_id, n, fields,"
                    " script_blocks) VALUES (:aid, :cid, 1,"
                    """ '[{"id": 1, "key": "first_name", "kind": "input",
                         "required": true, "type": "text", "values": [],
                         "instructions": "", "default": ""}]', '[]') RETURNING id"""
                ),
                {"aid": str(agent_id), "cid": str(client_id)},
            )
        ).scalar_one()
        await s.execute(
            text("UPDATE agent SET current_version_id = :vid WHERE id = :aid"),
            {"vid": str(version_id), "aid": str(agent_id)},
        )

    sign_in(client_plane, OWNER)
    csv = b"phone,first_name\n+2348031925030,Chidinma\nnot-a-number,Efe\n"
    response = client_plane.post(
        "/contacts/upload",
        data={
            # The pick screen renders upload forms only for pickable
            # agents; any page with a form carries the session's token.
            "csrf_token": csrf_from(client_plane.get("/notifications").text),
            "agent_id": str(agent_id),
        },
        files={"file": ("leads.csv", csv, "text/csv")},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    async with db.console_session() as s:
        row = (
            await s.execute(
                text("SELECT event, body FROM notification WHERE event = 'import_blocked'")
            )
        ).first()
    assert row is not None
    assert "1 of 2" in row[1]
