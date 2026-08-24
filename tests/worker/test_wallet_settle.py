"""Settlement through the webhook path: once, at the snapshot rate,
for run calls and test calls alike."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from becca.config import load_settings
from becca.db.session import SessionFactory
from becca.services import ingest
from becca.telnyx.fake_gateway import FakeTelnyxGateway
from becca.worker.dispatch import dispatch_tick
from becca.worker.housekeeping import fail_stale_test_runs
from tests.worker.conftest import seed_run

NOW = datetime(2026, 8, 12, 10, 0, tzinfo=UTC)


async def _one(db: SessionFactory, sql: str, **params: Any) -> Any:
    async with db.worker_session() as s:
        return (await s.execute(text(sql), params)).scalar_one()


async def test_completed_callback_settles_exactly_once(db: SessionFactory) -> None:
    run = await seed_run(db, contacts=1, balance=100.0)
    gateway = FakeTelnyxGateway()
    assert await dispatch_tick(db, gateway, load_settings(), now_utc=NOW) == 1

    event = {
        "CallSid": gateway.calls[0]["call_control_id"],
        "CallStatus": "completed",
        "CallDuration": "95",  # 95s x 0.30/60 = 0.475 -> 0.48 per second
        "SequenceNumber": "3",
    }
    async with db.worker_session() as s:
        assert await ingest.ingest_texml_callback(s, event) is True
    # Redelivery: same event again must not debit twice.
    async with db.worker_session() as s:
        assert await ingest.ingest_texml_callback(s, dict(event)) is False

    balance = await _one(
        db,
        "SELECT wallet_balance_usd FROM client_account WHERE id = :cid",
        cid=str(run.client_id),
    )
    assert float(balance) == 99.52  # 100 - 0.48, once
    entries = await _one(
        db,
        "SELECT count(*) FROM wallet_ledger WHERE entry_type = 'debit_call'"
        " AND client_account_id = :cid",
        cid=str(run.client_id),
    )
    assert int(entries) == 1


async def test_failed_call_is_never_billed(db: SessionFactory) -> None:
    run = await seed_run(db, contacts=1, balance=100.0)
    gateway = FakeTelnyxGateway()
    assert await dispatch_tick(db, gateway, load_settings(), now_utc=NOW) == 1
    async with db.worker_session() as s:
        await ingest.ingest_texml_callback(
            s,
            {
                "CallSid": gateway.calls[0]["call_control_id"],
                "CallStatus": "no-answer",
                "SequenceNumber": "2",
            },
        )
    balance = await _one(
        db,
        "SELECT wallet_balance_usd FROM client_account WHERE id = :cid",
        cid=str(run.client_id),
    )
    assert float(balance) == 100.0
    entries = await _one(db, "SELECT count(*) FROM wallet_ledger WHERE entry_type != 'credit'")
    assert int(entries) == 0


async def _seed_test_run(
    db: SessionFactory, run: Any, *, sid: str | None, minutes_old: int = 0, n: int = 1
) -> Any:
    async with db.console_session() as s:
        version_id = (
            await s.execute(
                text("SELECT current_version_id FROM agent WHERE id = :aid"),
                {"aid": str(run.agent_id)},
            )
        ).scalar_one()
        return (
            await s.execute(
                text(
                    "INSERT INTO test_run (agent_id, client_account_id, agent_version_id,"
                    " n, schema_snapshot, stand_ins, to_number, status, rate_per_min_usd,"
                    " telnyx_call_sid, created_at)"
                    " VALUES (:aid, :cid, :vid, :n, '{}', '{}', '+2348012345678',"
                    " 'dialling', 0.30, :sid, now() - make_interval(mins => :old))"
                    " RETURNING id"
                ),
                {
                    "aid": str(run.agent_id),
                    "cid": str(run.client_id),
                    "vid": str(version_id),
                    "n": n,
                    "sid": sid,
                    "old": minutes_old,
                },
            )
        ).scalar_one()


async def test_test_call_callback_settles_without_touching_status(db: SessionFactory) -> None:
    run = await seed_run(db, contacts=1, balance=100.0)
    test_run_id = await _seed_test_run(db, run, sid="test-sid-1")
    event = {
        "CallSid": "test-sid-1",
        "CallStatus": "completed",
        "CallDuration": "61",  # 61s x 0.30/60 = 0.305 -> 0.31
        "SequenceNumber": "3",
    }
    async with db.worker_session() as s:
        assert await ingest.ingest_texml_callback(s, event) is True
    async with db.worker_session() as s:
        row = (
            await s.execute(
                text("SELECT status, duration_sec FROM test_run WHERE id = :id"),
                {"id": str(test_run_id)},
            )
        ).one()
    # The webhook billed it but the polling loop still owns the status.
    assert row[0] == "dialling"
    assert row[1] == 61
    balance = await _one(
        db,
        "SELECT wallet_balance_usd FROM client_account WHERE id = :cid",
        cid=str(run.client_id),
    )
    assert float(balance) == 99.69
    entry_type = await _one(
        db, "SELECT entry_type FROM wallet_ledger WHERE test_run_id = :id", id=str(test_run_id)
    )
    assert entry_type == "debit_test_call"


async def test_low_wallet_settle_warns_client_and_staff(db: SessionFactory) -> None:
    # Warn line = 2 waves x allocation 2 x (0.30 x 15) = 18.00; a settle
    # that lands the balance at 9.40 must warn both audiences once.
    run = await seed_run(db, contacts=1, allocation=2, balance=10.0)
    gateway = FakeTelnyxGateway()
    assert await dispatch_tick(db, gateway, load_settings(), now_utc=NOW) == 1
    async with db.worker_session() as s:
        await ingest.ingest_texml_callback(
            s,
            {
                "CallSid": gateway.calls[0]["call_control_id"],
                "CallStatus": "completed",
                "CallDuration": "95",
                "SequenceNumber": "3",
            },
        )
    client_rows = await _one(
        db,
        "SELECT count(*) FROM notification WHERE event = 'wallet_low' AND client_account_id = :cid",
        cid=str(run.client_id),
    )
    staff_rows = await _one(
        db,
        "SELECT count(*) FROM notification WHERE event = 'client_wallet_low'"
        " AND client_account_id IS NULL",
    )
    assert int(client_rows) == 1
    assert int(staff_rows) == 1


async def test_stale_test_runs_are_swept(db: SessionFactory) -> None:
    run = await seed_run(db, contacts=1)
    fresh = await _seed_test_run(db, run, sid="fresh-sid", minutes_old=5, n=1)
    stale = await _seed_test_run(db, run, sid="stale-sid", minutes_old=20, n=2)
    assert await fail_stale_test_runs(db) == 1
    async with db.worker_session() as s:
        rows = dict((await s.execute(text("SELECT id, status FROM test_run"))).all())
    assert rows[fresh] == "dialling"
    assert rows[stale] == "failed"
