"""results_tick: completed calls get their results mirrored (FR-RESULT-1)."""

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from becca.config import load_settings
from becca.db.session import SessionFactory
from becca.services import ingest
from becca.services.results import results_tick
from becca.telnyx.fake_gateway import FakeTelnyxGateway
from becca.worker.dispatch import dispatch_tick
from tests.worker.conftest import SeededRun, seed_run

NOW = datetime(2026, 8, 12, 10, 0, tzinfo=UTC)


async def _wire_run_objects(db: SessionFactory, gateway: FakeTelnyxGateway, run: SeededRun) -> None:
    """Build the run assistant on the fake gateway the way launch does,
    and store its ids + insight map on the seeded agent."""
    group = await gateway.create_insight_group(name=f"run-{run.agent_id}")
    insight = await gateway.create_insight(
        name="outcome", instructions="Capture the outcome.", json_schema=None
    )
    await gateway.assign_insight_to_group(group_id=group, insight_id=insight)
    assistant = await gateway.create_assistant(
        name="run",
        model="moonshotai/Kimi-K2.6",
        instructions="plan",
        greeting="hello",
        voice="Telnyx.NaturalHD.astra",
        insight_group_id=group,
        dynamic_variables={},
    )
    async with db.console_session() as s:
        await s.execute(
            text(
                "UPDATE agent SET telnyx_run_assistant_id = :assistant,"
                " telnyx_run_texml_app_id = :app, telnyx_run_insight_map = :imap"
                " WHERE id = :aid"
            ),
            {
                "assistant": assistant.id,
                "app": assistant.default_texml_app_id,
                "imap": json.dumps({"3": insight}),  # field 3 = outcome
                "aid": str(run.agent_id),
            },
        )


async def _one(db: SessionFactory, sql: str, **params: Any) -> Any:
    async with db.worker_session() as s:
        return (await s.execute(text(sql), params)).scalar_one()


async def test_completed_call_gets_transcript_and_results(db: SessionFactory) -> None:
    run = await seed_run(db, contacts=1)
    gateway = FakeTelnyxGateway()
    await _wire_run_objects(db, gateway, run)

    assert await dispatch_tick(db, gateway, load_settings(), now_utc=NOW) == 1
    async with db.worker_session() as s:
        await ingest.ingest_texml_callback(
            s,
            {
                "CallSid": gateway.calls[0]["call_control_id"],
                "CallStatus": "completed",
                "CallDuration": "60",
                "SequenceNumber": "1",
            },
        )

    assert await results_tick(db, gateway) == 1
    field_key, value = None, None
    async with db.worker_session() as s:
        field_key, value = (
            await s.execute(
                text("SELECT field_key, value FROM insight_result WHERE agent_id = :aid"),
                {"aid": str(run.agent_id)},
            )
        ).one()
    assert field_key == "outcome"
    assert value  # the fake's free-text summary
    messages = await _one(
        db, "SELECT messages FROM transcript WHERE agent_id = :aid", aid=str(run.agent_id)
    )
    parsed = messages if isinstance(messages, list) else json.loads(messages)
    assert len(parsed) == 2  # the fake conversation, mirrored
    conversation = await _one(
        db,
        "SELECT telnyx_conversation_id FROM call WHERE agent_id = :aid",
        aid=str(run.agent_id),
    )
    assert conversation  # the exact join was persisted

    # Idempotent: a second tick ingests nothing and duplicates nothing.
    assert await results_tick(db, gateway) == 0
    n = await _one(
        db, "SELECT count(*) FROM insight_result WHERE agent_id = :aid", aid=str(run.agent_id)
    )
    assert int(n) == 1


async def test_vanished_conversation_stops_being_polled(db: SessionFactory) -> None:
    """Telnyx garbage-collects some conversations (spike finding): after
    the give-up window an empty mirror is written so the poll ends."""
    run = await seed_run(db, contacts=1)
    gateway = FakeTelnyxGateway()
    await _wire_run_objects(db, gateway, run)

    async with db.console_session() as s:
        await s.execute(
            text(
                "INSERT INTO call (agent_id, client_account_id, status,"
                " telnyx_call_control_id, ended_at, idempotency_key)"
                " VALUES (:aid, :cid, 'completed', 'sid-vanished',"
                " now() - interval '11 minutes', 'k-vanished')"
            ),
            {"aid": str(run.agent_id), "cid": str(run.client_id)},
        )

    assert await results_tick(db, gateway) == 0
    async with db.worker_session() as s:
        messages, results = (
            await s.execute(
                text(
                    "SELECT t.messages, (SELECT count(*) FROM insight_result r"
                    " WHERE r.agent_id = :aid)"
                    " FROM transcript t WHERE t.agent_id = :aid"
                ),
                {"aid": str(run.agent_id)},
            )
        ).one()
    parsed = messages if isinstance(messages, list) else json.loads(messages)
    assert parsed == []  # the absence itself is mirrored
    assert int(results) == 0

    # And it is never polled again.
    assert await results_tick(db, gateway) == 0
