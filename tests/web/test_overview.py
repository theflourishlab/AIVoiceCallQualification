"""The Overview screen: today's numbers from real runs, honest empty
states, refresh only while dialling, tenant scoping."""

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from starlette.applications import Starlette

from becca.config import load_settings
from becca.db.session import SessionFactory
from becca.services import ingest
from becca.worker.dispatch import dispatch_tick
from tests.web.conftest import STAFF_EMAIL, csrf_from, sign_in
from tests.web.test_launch_flow import OWNER, _allocate_channels, _list_id, _setup
from tests.web.test_results_flow import _gateway_of, _launch_and_complete_all

NOW = datetime(2026, 8, 12, 10, 0, tzinfo=UTC)


async def test_overview_shows_todays_numbers(
    db: SessionFactory, app: Starlette, console: TestClient, client_plane: TestClient
) -> None:
    await _launch_and_complete_all(db, app, console, client_plane)
    page = client_plane.get("/overview")
    assert page.status_code == 200
    # 2 contacts completed at 60s (reached 2 of 2 -> 100%) plus the
    # _setup test call, which the harness's callback loop also settles:
    # 3 billed minutes x $0.30 = $0.90 spend. Calls today counts run
    # calls only; spend is the whole ledger — test calls are billed.
    assert "Calls today" in page.text
    assert "100%" in page.text
    assert "2 of 2 connected" in page.text
    assert "$0.90" in page.text
    assert "3 billed min" in page.text
    assert "$0.30/min" in page.text
    # The agents table (variant B rows): run totals and the finished pill.
    assert "Visit Qualifier" in page.text
    assert "2/2" in page.text
    assert "Complete" in page.text
    assert "Results captured" in page.text
    # Charts render with data; the quiet fallbacks are absent.
    assert "No calls yet today" not in page.text
    assert "Reached 100%" in page.text
    # Nothing is dialling any more: static page, no refresh tag.
    assert 'http-equiv="refresh"' not in page.text


async def test_overview_refreshes_only_while_dialling(
    db: SessionFactory, app: Starlette, console: TestClient, client_plane: TestClient
) -> None:
    agent_id = _setup(console, client_plane, db)
    page = client_plane.get(f"/agents/{agent_id}/launch").text
    client_plane.post(
        f"/agents/{agent_id}/acknowledge",
        data={"csrf_token": csrf_from(page)},
        follow_redirects=False,
    )
    await _allocate_channels(db)
    page = client_plane.get(f"/agents/{agent_id}/launch").text
    client_plane.post(
        f"/agents/{agent_id}/launch",
        data={
            "csrf_token": csrf_from(page),
            "list_id": _list_id(page),
            "window_start": "00:00",
            "window_end": "23:59",
            **{f"day_{d}": "1" for d in range(1, 8)},
            "spend_cap": "50",
        },
        follow_redirects=False,
    )
    gateway = _gateway_of(app)
    assert await dispatch_tick(db, gateway, load_settings(), now_utc=NOW) == 2

    # Mid-run: two calls in the air -> live bar, refresh tag, dialling slice.
    page = client_plane.get("/overview")
    assert 'http-equiv="refresh"' in page.text
    assert "channels in use" in page.text
    assert "Still dialling" in page.text
    assert "Dialling" in page.text  # the agent's status pill

    for call in gateway.calls:
        async with db.worker_session() as s:
            await ingest.ingest_texml_callback(
                s,
                {
                    "CallSid": call["call_control_id"],
                    "CallStatus": "completed",
                    "CallDuration": "60",
                    "SequenceNumber": "9",
                },
            )
    # One more tick lets the dispatcher observe the drained queue and
    # finish the run — only then does the Overview go static.
    await dispatch_tick(db, gateway, load_settings(), now_utc=NOW)
    page = client_plane.get("/overview")
    assert 'http-equiv="refresh"' not in page.text
    assert "Nothing is dialling right now." in page.text


async def test_overview_empty_state(
    db: SessionFactory, console: TestClient, client_plane: TestClient
) -> None:
    from tests.web.test_agent_flow import _create_client_account

    _create_client_account(console, OWNER)
    sign_in(client_plane, OWNER)
    page = client_plane.get("/overview")
    assert page.status_code == 200
    assert "Quiet" in page.text
    assert "Nothing has launched yet" in page.text
    assert "No calls yet today" in page.text
    assert "Outcomes appear as calls complete." in page.text


async def test_overview_is_tenant_scoped(
    db: SessionFactory, app: Starlette, console: TestClient, client_plane: TestClient
) -> None:
    await _launch_and_complete_all(db, app, console, client_plane)
    # A second client's owner sees their own (empty) Overview, nothing
    # of Sylvastar's run — RLS plus the service's own scoping.
    sign_in(console, STAFF_EMAIL)
    token = csrf_from(console.get("/clients/new").text)
    console.post(
        "/clients",
        data={
            "csrf_token": token,
            "name": "Lekki Gardens",
            "rate_per_min": "0.30",
            "owner_email": "lekki-owner@becca.live",
        },
        follow_redirects=False,
    )
    sign_in(client_plane, "lekki-owner@becca.live")
    page = client_plane.get("/overview")
    assert "Nothing has launched yet" in page.text
    assert "Visit Qualifier" not in page.text
    assert "$0.60" not in page.text


async def test_nav_links_overview(
    db: SessionFactory, console: TestClient, client_plane: TestClient
) -> None:
    from tests.web.test_agent_flow import _create_client_account

    _create_client_account(console, OWNER)
    sign_in(client_plane, OWNER)
    home = client_plane.get("/")
    assert 'href="/overview"' in home.text
