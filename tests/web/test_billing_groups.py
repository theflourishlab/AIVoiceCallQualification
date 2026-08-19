"""FR-BILL-3: one billing group per client, attached to their numbers,
created at assignment time and backfillable for older assignments."""

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import text
from starlette.applications import Starlette

from becca.db.session import SessionFactory
from becca.telnyx.fake_gateway import FakeTelnyxGateway
from tests.web.conftest import STAFF_EMAIL, csrf_from, sign_in
from tests.web.test_console_flow import _create_client_account


def _gateway(app: Starlette) -> FakeTelnyxGateway:
    # Both planes share the one gateway instance built in create_app.
    for route in app.routes:
        state = getattr(route.app, "state", None) if hasattr(route, "app") else None
        if state is not None and hasattr(state, "telnyx"):
            gateway = state.telnyx
            assert isinstance(gateway, FakeTelnyxGateway)
            return gateway
    raise AssertionError("no plane app with a telnyx gateway")


async def _assign(
    console: TestClient, db: SessionFactory, *, e164: str, client_id: uuid.UUID | str | None
) -> None:
    page = console.get("/numbers")  # syncs inventory from the fake account
    async with db.console_session() as s:
        number_id = (
            await s.execute(text("SELECT id FROM phone_number WHERE phone_e164 = :e"), {"e": e164})
        ).scalar_one()
    response = console.post(
        f"/numbers/{number_id}/assign",
        data={
            "csrf_token": csrf_from(page.text),
            "client_id": str(client_id) if client_id else "",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    assert "done=assigned" in response.headers["location"]
    assert "warn" not in response.headers["location"]


async def test_assignment_creates_group_and_attaches_number(
    app: Starlette, console: TestClient, db: SessionFactory
) -> None:
    sign_in(console, STAFF_EMAIL)
    _create_client_account(console)
    async with db.console_session() as s:
        client_id = (await s.execute(text("SELECT id FROM client_account"))).scalar_one()

    await _assign(console, db, e164="+2342093940544", client_id=client_id)

    async with db.console_session() as s:
        group_id = (
            await s.execute(text("SELECT telnyx_billing_group_id FROM client_account"))
        ).scalar_one()
    assert group_id, "no billing group recorded on the client"

    gateway = _gateway(app)
    assert group_id in gateway.billing_groups
    number = next(n for n in gateway.numbers if n["phone_number"] == "+2342093940544")
    assert number["billing_group_id"] == group_id

    async with db.console_session() as s:
        actions = (await s.execute(text("SELECT action FROM audit_log"))).scalars().all()
    assert "ensured_billing_group" in actions


async def test_reassignment_moves_number_between_groups(
    app: Starlette, console: TestClient, db: SessionFactory
) -> None:
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
    async with db.console_session() as s:
        sylvastar = (
            await s.execute(text("SELECT id FROM client_account WHERE name = 'Sylvastar'"))
        ).scalar_one()
        lekki = (
            await s.execute(text("SELECT id FROM client_account WHERE name = 'Lekki Gardens'"))
        ).scalar_one()

    await _assign(console, db, e164="+2342093940544", client_id=sylvastar)
    await _assign(console, db, e164="+2342093940544", client_id=lekki)

    async with db.console_session() as s:
        groups = dict(
            (
                await s.execute(text("SELECT name, telnyx_billing_group_id FROM client_account"))
            ).all()
        )
    assert groups["Sylvastar"] and groups["Lekki Gardens"]
    assert groups["Sylvastar"] != groups["Lekki Gardens"]

    gateway = _gateway(app)
    number = next(n for n in gateway.numbers if n["phone_number"] == "+2342093940544")
    assert number["billing_group_id"] == groups["Lekki Gardens"]

    # Unassigning detaches, so a later tenant's costs cannot land in the
    # previous client's group.
    await _assign(console, db, e164="+2342093940544", client_id=None)
    assert number["billing_group_id"] is None


async def test_backfill_prompt_for_pre_existing_assignments(
    app: Starlette, console: TestClient, db: SessionFactory
) -> None:
    """A number assigned before billing groups shipped shows the callout;
    the one-click ensure creates and attaches."""
    sign_in(console, STAFF_EMAIL)
    _create_client_account(console)
    console.get("/numbers")  # sync inventory
    async with db.console_session() as s:
        client_id = (await s.execute(text("SELECT id FROM client_account"))).scalar_one()
        # Assign directly in the DB — the pre-Phase-7 path, no group made.
        await s.execute(
            text(
                "UPDATE phone_number SET client_account_id = :cid, assigned_at = now()"
                " WHERE phone_e164 = '+2342093940544'"
            ),
            {"cid": str(client_id)},
        )

    page = console.get("/numbers")
    assert "no billing group" in page.text
    response = console.post(
        "/billing-groups/ensure",
        data={"csrf_token": csrf_from(page.text)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "done=groups" in response.headers["location"]

    async with db.console_session() as s:
        group_id = (
            await s.execute(text("SELECT telnyx_billing_group_id FROM client_account"))
        ).scalar_one()
    assert group_id
    gateway = _gateway(app)
    number = next(n for n in gateway.numbers if n["phone_number"] == "+2342093940544")
    assert number["billing_group_id"] == group_id

    # Satisfied: the prompt is gone.
    page = console.get("/numbers")
    assert "no billing group" not in page.text
