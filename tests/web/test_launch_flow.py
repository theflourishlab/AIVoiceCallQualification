"""The Phase 4 gate, web side: describe -> test -> import -> launch."""

import json

from fastapi.testclient import TestClient
from sqlalchemy import text

from becca.db.session import SessionFactory
from tests.web.conftest import STAFF_EMAIL, csrf_from, fund_wallet, sign_in

OWNER = "sylvester@sylvastar.ng"

CSV = "First Name,Phone,Visit Date\nChidinma,0803 000 1188,22 Aug\nAdaeze,0803 000 1189,23 Aug\n"


def _setup(console: TestClient, client_plane: TestClient, db_url_marker: object) -> str:
    """Client account + agent, test called, contacts imported. Returns
    the agent id."""
    sign_in(console, STAFF_EMAIL)
    token = csrf_from(console.get("/clients/new").text)
    console.post(
        "/clients",
        data={
            "csrf_token": token,
            "name": "Sylvastar",
            "rate_per_min": "0.30",
            "owner_email": OWNER,
        },
    )
    fund_wallet(console)
    sign_in(client_plane, OWNER)
    token = csrf_from(client_plane.get("/agents/new").text)
    agent_url = client_plane.post(
        "/agents",
        data={"csrf_token": token, "brief": "Visit Qualifier"},
        follow_redirects=False,
    ).headers["location"]
    agent_id = agent_url.rsplit("/", 1)[1]

    # A real test call through the fake gateway satisfies "tested".
    token = csrf_from(client_plane.get(f"/agents/{agent_id}/test").text)
    client_plane.post(
        f"/agents/{agent_id}/test-calls",
        data={
            "csrf_token": token,
            "to_number": "+2348031925030",
            "standin_first_name": "Chidinma",
            "standin_visit_date": "22 Aug",
        },
        follow_redirects=False,
    )

    token = csrf_from(client_plane.get(f"/contacts?agent={agent_id}").text)
    client_plane.post(
        "/contacts/upload",
        data={"csrf_token": token, "agent_id": agent_id},
        files={"file": ("visits.csv", CSV.encode(), "text/csv")},
        follow_redirects=False,
    )
    return agent_id


async def _allocate_channels(db: SessionFactory, n: int = 2) -> None:
    async with db.console_session() as s:
        await s.execute(text("UPDATE client_account SET channel_allocation = :n"), {"n": n})


async def test_preflight_blocks_then_launch_succeeds(
    db: SessionFactory, console: TestClient, client_plane: TestClient
) -> None:
    agent_id = _setup(console, client_plane, db)

    # Pre-flight refuses: no acknowledgement, no channels.
    page = client_plane.get(f"/agents/{agent_id}/launch").text
    assert "blocking" in page
    assert "Before your first launch" in page  # FR-CONSOLE-2A prompt

    # A stale browser cannot launch past the server-side re-check.
    token = csrf_from(page)
    refused = client_plane.post(
        f"/agents/{agent_id}/launch",
        data={
            "csrf_token": token,
            "list_id": _list_id(page),
            "window_start": "09:00",
            "window_end": "17:00",
            "day_1": "1",
            "spend_cap": "50",
        },
        follow_redirects=False,
    )
    assert "error=preflight" in refused.headers["location"]

    # Owner accepts the acknowledgement; console allocates channels.
    client_plane.post(
        f"/agents/{agent_id}/acknowledge",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    await _allocate_channels(db)

    page = client_plane.get(f"/agents/{agent_id}/launch").text
    assert "Ready" in page

    launched = client_plane.post(
        f"/agents/{agent_id}/launch",
        data={
            "csrf_token": csrf_from(page),
            "list_id": _list_id(page),
            "window_start": "09:00",
            "window_end": "17:00",
            "day_1": "1",
            "day_2": "1",
            "day_3": "1",
            "day_4": "1",
            "day_5": "1",
            "retry_attempts": "1",
            "retry_interval_secs": "3600",
            "spend_cap": "50",
        },
        follow_redirects=False,
    )
    assert launched.status_code == 303
    assert "error" not in launched.headers["location"]

    async with db.console_session() as s:
        status = (
            await s.execute(text("SELECT status FROM agent WHERE id = :aid"), {"aid": agent_id})
        ).scalar_one()
        queued = (
            await s.execute(
                text("SELECT count(*) FROM queue_item WHERE agent_id = :aid"),
                {"aid": agent_id},
            )
        ).scalar_one()
        run_assistant = (
            await s.execute(
                text("SELECT telnyx_run_assistant_id FROM agent WHERE id = :aid"),
                {"aid": agent_id},
            )
        ).scalar_one()
    assert status == "dialling"
    assert int(queued) == 2  # both diallable contacts
    assert run_assistant  # steps 2-5 built the run objects

    status_page = client_plane.get(f"/agents/{agent_id}/launch").text
    assert "Run status" in status_page
    assert "Pause" in status_page


async def test_launched_agent_is_frozen(
    db: SessionFactory, console: TestClient, client_plane: TestClient
) -> None:
    """FR-LAUNCH-7: no new versions once launched — duplicate instead."""
    agent_id = _setup(console, client_plane, db)
    page = client_plane.get(f"/agents/{agent_id}/launch").text
    token = csrf_from(page)
    client_plane.post(
        f"/agents/{agent_id}/acknowledge", data={"csrf_token": token}, follow_redirects=False
    )
    await _allocate_channels(db)
    page = client_plane.get(f"/agents/{agent_id}/launch").text
    client_plane.post(
        f"/agents/{agent_id}/launch",
        data={
            "csrf_token": csrf_from(page),
            "list_id": _list_id(page),
            "window_start": "09:00",
            "window_end": "17:00",
            "day_1": "1",
            "spend_cap": "50",
        },
        follow_redirects=False,
    )

    content = {
        "fields": [{"id": 1, "key": "renamed", "kind": "input", "required": True, "type": "text"}],
        "script_blocks": [{"type": "field_ref", "field_id": 1}],
    }
    review = client_plane.get(f"/agents/{agent_id}").text
    client_plane.post(
        f"/agents/{agent_id}/versions",
        data={"csrf_token": csrf_from(review), "content_json": json.dumps(content)},
        follow_redirects=False,
    )
    async with db.console_session() as s:
        versions = (
            await s.execute(
                text("SELECT count(*) FROM agent_version WHERE agent_id = :aid"),
                {"aid": agent_id},
            )
        ).scalar_one()
    assert int(versions) == 1  # still only v1


async def test_stop_drains_the_queue(
    db: SessionFactory, console: TestClient, client_plane: TestClient
) -> None:
    agent_id = _setup(console, client_plane, db)
    page = client_plane.get(f"/agents/{agent_id}/launch").text
    token = csrf_from(page)
    client_plane.post(
        f"/agents/{agent_id}/acknowledge", data={"csrf_token": token}, follow_redirects=False
    )
    await _allocate_channels(db)
    page = client_plane.get(f"/agents/{agent_id}/launch").text
    client_plane.post(
        f"/agents/{agent_id}/launch",
        data={
            "csrf_token": csrf_from(page),
            "list_id": _list_id(page),
            "window_start": "09:00",
            "window_end": "17:00",
            "day_1": "1",
            "spend_cap": "50",
        },
        follow_redirects=False,
    )

    status_page = client_plane.get(f"/agents/{agent_id}/launch").text
    client_plane.post(
        f"/agents/{agent_id}/stop",
        data={"csrf_token": csrf_from(status_page)},
        follow_redirects=False,
    )
    async with db.console_session() as s:
        status = (
            await s.execute(text("SELECT status FROM agent WHERE id = :aid"), {"aid": agent_id})
        ).scalar_one()
        pending = (
            await s.execute(
                text("SELECT count(*) FROM queue_item WHERE agent_id = :aid AND state = 'pending'"),
                {"aid": agent_id},
            )
        ).scalar_one()
        skipped = (
            await s.execute(
                text("SELECT count(*) FROM queue_item WHERE agent_id = :aid AND state = 'skipped'"),
                {"aid": agent_id},
            )
        ).scalar_one()
    assert status == "finished"
    assert int(pending) == 0
    assert int(skipped) == 2  # undialled contacts stay undialled (FR-DISPATCH-7)


def _list_id(page: str) -> str:
    import re

    m = re.search(r'<option value="([0-9a-f-]{36})"', page)
    assert m, "no contact list option on the launch page"
    return m.group(1)
