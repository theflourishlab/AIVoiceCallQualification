"""dispatch_tick against a seeded queue — the FRD's worker guarantees."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from becca.config import load_settings
from becca.db.session import SessionFactory
from becca.services import ingest
from becca.telnyx.fake_gateway import FakeTelnyxGateway
from becca.telnyx.gateway import DialRefused, TelnyxError
from becca.worker.dispatch import dispatch_tick
from becca.worker.housekeeping import reconcile_stuck_dialling
from tests.worker.conftest import seed_run

NOW = datetime(2026, 8, 12, 10, 0, tzinfo=UTC)  # a Wednesday, 11:00 in Lagos


async def _tick(db: SessionFactory, gateway: FakeTelnyxGateway) -> int:
    return await dispatch_tick(db, gateway, load_settings(), now_utc=NOW)


async def _one(db: SessionFactory, sql: str, **params: Any) -> Any:
    async with db.worker_session() as s:
        return (await s.execute(text(sql), params)).scalar_one()


async def test_dials_up_to_the_allocation_and_no_further(db: SessionFactory) -> None:
    run = await seed_run(db, contacts=3, allocation=2)
    gateway = FakeTelnyxGateway()

    placed = await _tick(db, gateway)
    assert placed == 2  # the governor's ceiling, not the queue's length
    assert len(gateway.calls) == 2
    assert gateway.calls[0]["variables"] == {
        "first_name": "Chidinma",
        "consultant_name": "a consultant",  # optional default (FR-CONTACT-9)
    }
    assert gateway.calls[0]["record"] is False  # client setting at dial time

    # Nothing more while both channels are occupied.
    assert await _tick(db, gateway) == 0

    # One call completes (status callback) -> one channel frees.
    async with db.worker_session() as s:
        await ingest.ingest_texml_callback(
            s,
            {
                "CallSid": gateway.calls[0]["call_control_id"],
                "CallStatus": "completed",
                "CallDuration": "95",
                "CallSessionId": "sess-1",
                "SequenceNumber": "3",
            },
        )
    assert await _tick(db, gateway) == 1
    done = await _one(
        db,
        "SELECT count(*) FROM queue_item WHERE agent_id = :aid AND state = 'done'",
        aid=str(run.agent_id),
    )
    assert int(done) == 1


async def test_closed_window_dials_nothing(db: SessionFactory) -> None:
    await seed_run(db, window=("07:00", "08:00"))  # Lagos is 11:00 at NOW
    gateway = FakeTelnyxGateway()
    assert await _tick(db, gateway) == 0
    assert gateway.calls == []


async def test_paused_run_dials_nothing(db: SessionFactory) -> None:
    await seed_run(db, paused=True)
    gateway = FakeTelnyxGateway()
    assert await _tick(db, gateway) == 0
    assert gateway.calls == []


async def test_spend_cap_pauses_instead_of_dialling(db: SessionFactory) -> None:
    run = await seed_run(db, spend_cap=0.0)
    gateway = FakeTelnyxGateway()
    assert await _tick(db, gateway) == 0
    assert gateway.calls == []
    paused = await _one(
        db, "SELECT paused FROM run_schedule WHERE agent_id = :aid", aid=str(run.agent_id)
    )
    assert bool(paused)  # FR-DISPATCH-10: paused, nothing lost
    pending = await _one(
        db,
        "SELECT count(*) FROM queue_item WHERE agent_id = :aid AND state = 'pending'",
        aid=str(run.agent_id),
    )
    assert int(pending) == 3


async def test_empty_wallet_pauses_the_run(db: SessionFactory) -> None:
    """The prepaid inversion of FR-BILL-8: no balance, no dialling. The
    run pauses with its own event; nothing is lost, topping up and
    resuming continues where it left off."""
    run = await seed_run(db, balance=0.0)
    gateway = FakeTelnyxGateway()
    assert await _tick(db, gateway) == 0
    assert gateway.calls == []
    paused = await _one(
        db, "SELECT paused FROM run_schedule WHERE agent_id = :aid", aid=str(run.agent_id)
    )
    assert bool(paused)
    event = await _one(db, "SELECT event FROM notification WHERE client_account_id IS NOT NULL")
    assert event == "run_paused_no_balance"
    pending = await _one(
        db,
        "SELECT count(*) FROM queue_item WHERE agent_id = :aid AND state = 'pending'",
        aid=str(run.agent_id),
    )
    assert int(pending) == 3


async def test_wallet_covering_one_hold_dials_one_call(db: SessionFactory) -> None:
    # One hold is 0.30 x 15 = 4.50. A 4.50 wallet dials exactly one of
    # the two allocated channels, then the gate refuses the second.
    run = await seed_run(db, contacts=3, allocation=2, balance=4.50)
    gateway = FakeTelnyxGateway()
    assert await _tick(db, gateway) == 1
    assert len(gateway.calls) == 1
    # Not paused: the pre-check saw cover for one call and the claim
    # gate simply stopped at the wallet's edge, like a full allocation.
    paused = await _one(
        db, "SELECT paused FROM run_schedule WHERE agent_id = :aid", aid=str(run.agent_id)
    )
    assert not bool(paused)


async def test_unresolved_variable_pauses_and_never_skips(db: SessionFactory) -> None:
    """FR-DISPATCH-11: an invariant assertion. The row is not dialled,
    the run stops, and no later row is attempted."""
    run = await seed_run(db, contacts=2, contact_variables={})  # first_name missing
    gateway = FakeTelnyxGateway()
    assert await _tick(db, gateway) == 0
    assert gateway.calls == []
    paused = await _one(
        db, "SELECT paused FROM run_schedule WHERE agent_id = :aid", aid=str(run.agent_id)
    )
    assert bool(paused)
    reason = await _one(
        db,
        "SELECT metadata->>'reason' FROM audit_log WHERE action = 'run_paused_by_worker'"
        " ORDER BY id DESC LIMIT 1",
    )
    assert "unresolved" in reason


async def test_dial_refused_pauses_the_run(db: SessionFactory) -> None:
    """SD-13 firing means non-production tried to dial a stranger: stop,
    never retry-loop."""

    class RefusingGateway(FakeTelnyxGateway):
        async def place_call(self, **kwargs: Any) -> dict[str, Any]:
            raise DialRefused(kwargs["to"], "dev")

    run = await seed_run(db)
    gateway = RefusingGateway()
    assert await _tick(db, gateway) == 0
    paused = await _one(
        db, "SELECT paused FROM run_schedule WHERE agent_id = :aid", aid=str(run.agent_id)
    )
    assert bool(paused)
    skipped = await _one(
        db,
        "SELECT count(*) FROM queue_item WHERE agent_id = :aid AND state = 'skipped'",
        aid=str(run.agent_id),
    )
    assert int(skipped) == 1  # only the refused row; the rest stay pending


async def test_failed_call_retries_then_gives_up(db: SessionFactory) -> None:
    run = await seed_run(db, contacts=1, retry_attempts=1)
    gateway = FakeTelnyxGateway()
    assert await _tick(db, gateway) == 1

    async def fail_latest() -> None:
        async with db.worker_session() as s:
            await ingest.ingest_texml_callback(
                s,
                {
                    "CallSid": gateway.calls[-1]["call_control_id"],
                    "CallStatus": "failed",
                    "SequenceNumber": str(len(gateway.calls)),
                },
            )

    await fail_latest()
    state, next_at = None, None
    async with db.worker_session() as s:
        state, next_at = (
            await s.execute(
                text("SELECT state, next_attempt_at FROM queue_item WHERE agent_id = :aid"),
                {"aid": str(run.agent_id)},
            )
        ).one()
    assert state == "pending"  # first failure -> one retry remains
    assert next_at is not None

    # Make the retry due now, dial and fail again -> exhausted.
    async with db.worker_session() as s:
        await s.execute(text("UPDATE queue_item SET next_attempt_at = now()"))
    assert await _tick(db, gateway) == 1
    await fail_latest()
    state = await _one(
        db, "SELECT state FROM queue_item WHERE agent_id = :aid", aid=str(run.agent_id)
    )
    assert state == "failed"


async def test_stuck_dialling_is_reconciled_by_timeout(db: SessionFactory) -> None:
    """Callbacks have been observed to just stop (spike-era mystery); a
    wedged channel must free itself."""
    run = await seed_run(db, contacts=1, retry_attempts=1)
    gateway = FakeTelnyxGateway()
    assert await _tick(db, gateway) == 1

    async with db.worker_session() as s:
        await s.execute(text("UPDATE queue_item SET last_attempt_at = now() - interval '1 hour'"))
    fixed = await reconcile_stuck_dialling(db)
    assert fixed == 1
    call_status = await _one(
        db, "SELECT status FROM call WHERE agent_id = :aid", aid=str(run.agent_id)
    )
    assert call_status == "failed"
    state = await _one(
        db, "SELECT state FROM queue_item WHERE agent_id = :aid", aid=str(run.agent_id)
    )
    assert state == "pending"  # freed channel, retry policy applied


async def test_drained_queue_finishes_the_run(db: SessionFactory) -> None:
    run = await seed_run(db, contacts=1, allocation=2)
    gateway = FakeTelnyxGateway()
    assert await _tick(db, gateway) == 1
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
    await _tick(db, gateway)
    status = await _one(db, "SELECT status FROM agent WHERE id = :aid", aid=str(run.agent_id))
    assert status == "finished"  # FR-DISPATCH-7's natural end


async def test_duplicate_callback_is_a_noop(db: SessionFactory) -> None:
    """SD-26: redelivery must not double-apply."""
    await seed_run(db, contacts=1)
    gateway = FakeTelnyxGateway()
    await _tick(db, gateway)
    event = {
        "CallSid": gateway.calls[0]["call_control_id"],
        "CallStatus": "completed",
        "CallDuration": "60",
        "SequenceNumber": "1",
    }
    async with db.worker_session() as s:
        assert await ingest.ingest_texml_callback(s, event) is True
    async with db.worker_session() as s:
        assert await ingest.ingest_texml_callback(s, dict(event)) is False


class _AccountDisabledGateway(FakeTelnyxGateway):
    """Every dial 403s the way an empty balance does (D17, findings §10)."""

    async def place_call(self, **kwargs: Any) -> dict[str, Any]:
        raise TelnyxError(403, '{"code": "10010", "title": "Account is disabled D17"}')


async def test_account_403_pauses_the_run_and_burns_no_retries(db: SessionFactory) -> None:
    """An account-level refusal is a run problem, not a contact problem:
    the run pauses on the FIRST 403, the claim is refunded (no call row,
    no attempt), and every contact stays pending for the resume."""
    run = await seed_run(db, contacts=3, allocation=2, retry_attempts=2)
    gateway = _AccountDisabledGateway()

    assert await _tick(db, gateway) == 0
    # One 403 was enough: nothing was marked failed or skipped, nobody's
    # retry budget moved, and the triggering claim left no call row.
    pending = await _one(
        db,
        "SELECT count(*) FROM queue_item WHERE agent_id = :aid"
        " AND state = 'pending' AND attempts = 0",
        aid=str(run.agent_id),
    )
    assert int(pending) == 3
    calls = await _one(db, "SELECT count(*) FROM call WHERE agent_id = :aid", aid=str(run.agent_id))
    assert int(calls) == 0
    paused = await _one(
        db, "SELECT paused FROM run_schedule WHERE agent_id = :aid", aid=str(run.agent_id)
    )
    assert bool(paused)
    reason = await _one(
        db,
        "SELECT metadata->>'reason' FROM audit_log WHERE action = 'run_paused_by_worker'"
        " ORDER BY id DESC LIMIT 1",
    )
    assert "403" in str(reason)

    # A human fixes the account and resumes: dialling just works again.
    async with db.worker_session() as s:
        await s.execute(
            text("UPDATE run_schedule SET paused = false WHERE agent_id = :aid"),
            {"aid": str(run.agent_id)},
        )
    healthy = FakeTelnyxGateway()
    assert await _tick(db, healthy) == 2  # allocation ceiling, budgets intact


async def test_pause_and_finish_emit_notifications(db: SessionFactory) -> None:
    """FR-NOTIFY-2B via the worker: spend cap -> its own event; a
    drained run -> run_finished. One row each, client-addressed."""
    run = await seed_run(db, contacts=1, spend_cap=0.0)
    gateway = FakeTelnyxGateway()
    await _tick(db, gateway)  # spend cap pauses before any dial
    event = await _one(
        db,
        "SELECT event FROM notification WHERE client_account_id IS NOT NULL",
    )
    assert event == "spend_cap_reached"

    # Unpause with a workable cap; dial, complete, drain -> finished.
    async with db.worker_session() as s:
        await s.execute(
            text("UPDATE run_schedule SET paused = false, spend_cap = 99 WHERE agent_id = :aid"),
            {"aid": str(run.agent_id)},
        )
    await _tick(db, gateway)
    async with db.worker_session() as s:
        await ingest.ingest_texml_callback(
            s,
            {
                "CallSid": gateway.calls[0]["call_control_id"],
                "CallStatus": "completed",
                "CallDuration": "95",
                "CallSessionId": "sess-n1",
                "SequenceNumber": "3",
            },
        )
    await _tick(db, gateway)  # observes the drained queue
    finished = await _one(db, "SELECT count(*) FROM notification WHERE event = 'run_finished'")
    assert int(finished) == 1
