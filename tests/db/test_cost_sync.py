"""FR-BILL-2/4: cost sync joins detail records to calls on the per-type
session-id key and fills cost_actual, AMD included (spike findings §11)."""

import uuid

from sqlalchemy import text

from becca.db.session import SessionFactory
from becca.services import billing
from becca.telnyx.fake_gateway import FakeTelnyxGateway
from tests.db.conftest import seed_client


async def _seed_call(
    factory: SessionFactory, *, client_id: uuid.UUID, agent_id: uuid.UUID, session_id: str
) -> None:
    async with factory.console_session() as s:
        await s.execute(
            text(
                "INSERT INTO call (agent_id, client_account_id, telnyx_call_session_id,"
                " status, started_at, idempotency_key)"
                " VALUES (:aid, :cid, :sid, 'completed', now(), :key)"
            ),
            {
                "aid": str(agent_id),
                "cid": str(client_id),
                "sid": session_id,
                "key": f"test-{session_id}",
            },
        )


async def test_cost_sync_fills_cost_actual(db: SessionFactory) -> None:
    client_id, agent_id = await seed_client(db, name="sylvastar")
    gateway = FakeTelnyxGateway()
    # Two calls through the fake gateway = two ai-voice-assistant records
    # (0.10 each) + two amd records (0.0065 each), keyed by session id.
    for n in range(2):
        await gateway.place_call(
            connection_id="fake-app",
            assistant_id="fake-assistant",
            to=f"+23480000000{n}",
            from_="+2342093940544",
            variables={},
            metadata={},
            record=False,
            status_callback="",
            amd_status_callback="",
        )
    session_ids = [c["call_session_id"] for c in gateway.calls]
    for sid in session_ids:
        await _seed_call(db, client_id=client_id, agent_id=agent_id, session_id=sid)

    async with db.console_session() as s:
        updated = await billing.sync_costs(s, gateway)
    assert updated == 2

    async with db.console_session() as s:
        costs = (
            (await s.execute(text("SELECT cost_actual FROM call ORDER BY telnyx_call_session_id")))
            .scalars()
            .all()
        )
    # Per call: ai-voice-assistant 0.10 + sip-trunking PSTN leg 0.234
    # + call-control 0.004 + amd 0.0065 (FR-BILL-2/4, findings §11a).
    assert [float(c) for c in costs] == [0.3445, 0.3445]

    # Idempotent: a second sync with unchanged records touches nothing.
    async with db.console_session() as s:
        assert await billing.sync_costs(s, gateway) == 0

    # A record that arrives late raises the total on the next sync.
    await gateway.place_call(
        connection_id="fake-app",
        assistant_id="fake-assistant",
        to="+2348000000009",
        from_="+2342093940544",
        variables={},
        metadata={},
        record=False,
        status_callback="",
        amd_status_callback="",
    )
    late_sid = gateway.calls[-1]["call_session_id"]
    await _seed_call(db, client_id=client_id, agent_id=agent_id, session_id=late_sid)
    async with db.console_session() as s:
        assert await billing.sync_costs(s, gateway) == 1


async def test_cost_sync_ignores_unknown_sessions(db: SessionFactory) -> None:
    """Records for calls we do not hold (another environment's dials,
    the pre-Becca spike) update nothing and break nothing."""
    await seed_client(db, name="sylvastar")
    gateway = FakeTelnyxGateway()
    await gateway.place_call(
        connection_id="fake-app",
        assistant_id="fake-assistant",
        to="+2348000000000",
        from_="+2342093940544",
        variables={},
        metadata={},
        record=False,
        status_callback="",
        amd_status_callback="",
    )
    async with db.console_session() as s:
        assert await billing.sync_costs(s, gateway) == 0
