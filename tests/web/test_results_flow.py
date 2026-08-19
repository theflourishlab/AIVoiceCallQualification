"""The Phase 5 gate: filterable, exportable results — driven end to end
through launch, dispatch, ingest, and the screens."""

from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from starlette.applications import Starlette

from becca.config import load_settings
from becca.db.session import SessionFactory
from becca.services import ingest
from becca.services.results import results_tick
from becca.worker.dispatch import dispatch_tick
from tests.web.conftest import csrf_from
from tests.web.test_launch_flow import _allocate_channels, _list_id, _setup

NOW = datetime(2026, 8, 12, 10, 0, tzinfo=UTC)


def _gateway_of(app: Starlette) -> Any:
    """The shared fake gateway lives on the plane apps' state."""
    for route in app.routes:
        inner = getattr(route, "app", None)
        if isinstance(inner, FastAPI) and hasattr(inner.state, "telnyx"):
            return inner.state.telnyx
    raise AssertionError("no plane app with a telnyx gateway")


async def _launch_and_complete_all(
    db: SessionFactory, app: Starlette, console: TestClient, client_plane: TestClient
) -> str:
    """Launch through the UI, then play the worker's part in-process."""
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
    settings = load_settings()
    while await dispatch_tick(db, gateway, settings, now_utc=NOW):
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
    assert await results_tick(db, gateway) > 0
    return agent_id


async def test_results_table_filters_views_and_csv(
    db: SessionFactory, app: Starlette, console: TestClient, client_plane: TestClient
) -> None:
    agent_id = await _launch_and_complete_all(db, app, console, client_plane)

    # Columns come from the frozen schema (FR-RESULT-2): FakeGenerator's
    # outputs are still_attending (enum, id 3) and summary (text, id 4).
    page = client_plane.get(f"/agents/{agent_id}/results").text
    assert "still_attending" in page and "summary" in page
    assert "+2348030001188" in page  # both contacts made it back
    assert "+2348030001189" in page

    # Enum filter: the fake scores every call "yes".
    filtered = client_plane.get(f"/agents/{agent_id}/results?f_3=yes").text
    assert "+2348030001188" in filtered
    empty = client_plane.get(f"/agents/{agent_id}/results?f_3=no").text
    assert "Nothing matches these filters" in empty

    # "Qualified" is a saved view, not a category (FR-RESULT-3).
    token = csrf_from(filtered)
    saved = client_plane.post(
        f"/agents/{agent_id}/views",
        data={"csrf_token": token, "name": "Qualified", "f_3": "yes"},
        follow_redirects=False,
    )
    assert saved.status_code == 303
    assert "Qualified" in client_plane.get(f"/agents/{agent_id}/results").text

    # CSV reflects the filtered view (FR-RESULT-5).
    csv_body = client_plane.get(f"/agents/{agent_id}/results.csv?f_3=yes").text
    assert "still_attending" in csv_body.splitlines()[0]
    assert "+2348030001188" in csv_body
    assert client_plane.get(f"/agents/{agent_id}/results.csv?f_3=no").text.count("\n") <= 1

    # The nav index lists the run.
    assert "Open results" in client_plane.get("/results").text


async def test_call_detail_correction_and_scoped_navigation(
    db: SessionFactory, app: Starlette, console: TestClient, client_plane: TestClient
) -> None:
    agent_id = await _launch_and_complete_all(db, app, console, client_plane)

    async with db.console_session() as s:
        call_ids = (
            (
                await s.execute(
                    text("SELECT id FROM call WHERE agent_id = :aid ORDER BY started_at DESC, id"),
                    {"aid": agent_id},
                )
            )
            .scalars()
            .all()
        )
    assert len(call_ids) == 2
    first = str(call_ids[0])

    detail = client_plane.get(f"/agents/{agent_id}/calls/{first}?f_3=yes").text
    assert "Transcript" in detail
    assert "Good afternoon" in detail  # mirrored fake conversation
    assert "1 OF 2 IN THIS VIEW" in detail  # scoped to the arrival filters
    assert "f_3=yes" in detail  # prev/next carry the filter set (FR-RESULT-8)

    # Correcting stores alongside, and the screen shows both.
    token = csrf_from(detail)
    client_plane.post(
        f"/agents/{agent_id}/calls/{first}/correct",
        data={
            "csrf_token": token,
            "field_id": "3",
            "corrected_value": "no",
            "return_qs": "",
        },
        follow_redirects=False,
    )
    corrected = client_plane.get(f"/agents/{agent_id}/calls/{first}").text
    assert "CORRECTED" in corrected
    assert "ORIGINAL: yes" in corrected

    # The filter now reads the corrected value (a correction changes
    # what the data says): 'no' finds this call, 'yes' no longer does.
    assert first in client_plane.get(f"/agents/{agent_id}/results?f_3=no").text
    assert first not in client_plane.get(f"/agents/{agent_id}/results?f_3=yes").text
