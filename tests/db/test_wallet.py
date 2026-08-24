"""The wallet service: ledger truth, cache discipline, settle idempotency."""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from becca.db.session import SessionFactory
from becca.services import wallet
from tests.db.conftest import seed_client

NIL = "00000000-0000-0000-0000-000000000000"


async def _insert_call(
    db: SessionFactory,
    *,
    client_id: uuid.UUID,
    agent_id: uuid.UUID,
    status: str = "completed",
    duration_sec: int | None = None,
    rate: float | None = 0.30,
    key: str | None = None,
) -> uuid.UUID:
    async with db.console_session() as s:
        return (
            await s.execute(
                text(
                    "INSERT INTO call (agent_id, client_account_id, status, duration_sec,"
                    " rate_per_min_usd, idempotency_key)"
                    " VALUES (:aid, :cid, :st, :dur, :rate, :key) RETURNING id"
                ),
                {
                    "aid": str(agent_id),
                    "cid": str(client_id),
                    "st": status,
                    "dur": duration_sec,
                    "rate": rate,
                    "key": key or f"run-{agent_id}-{uuid.uuid4()}-a1",
                },
            )
        ).scalar_one()


async def test_credit_writes_ledger_and_cache(db: SessionFactory) -> None:
    client_id, _ = await seed_client(db, name="sylvastar", balance=0)
    async with db.console_session() as s:
        entry = await wallet.credit(
            s,
            client_account_id=client_id,
            amount_usd=Decimal("50.00"),
            staff_id=uuid.UUID(NIL),
            note="GTB transfer ref 4411",
        )
    assert entry is not None
    async with db.console_session() as s:
        assert float(await wallet.balance(s, client_account_id=client_id)) == 50.0
        assert await wallet.verify(s, client_account_id=client_id)


async def test_credit_unknown_client_is_none(db: SessionFactory) -> None:
    async with db.console_session() as s:
        entry = await wallet.credit(
            s,
            client_account_id=uuid.uuid4(),
            amount_usd=Decimal("50.00"),
            staff_id=uuid.UUID(NIL),
            note="nope",
        )
    assert entry is None


async def test_settle_call_bills_per_second_and_moves_cache(db: SessionFactory) -> None:
    client_id, agent_id = await seed_client(db, name="sylvastar", balance=100)
    # 61s x 0.30/60 = 0.305 -> 0.31 (half-up to the cent); the ledger
    # line still shows 2 started minutes, but that is display, not price.
    call_id = await _insert_call(db, client_id=client_id, agent_id=agent_id, duration_sec=61)
    async with db.worker_session() as s:
        amount = await wallet.settle_call(s, call_id=call_id)
    assert amount is not None and float(amount) == -0.31
    async with db.console_session() as s:
        assert float(await wallet.balance(s, client_account_id=client_id)) == 99.69
        assert await wallet.verify(s, client_account_id=client_id)
        row = (
            await s.execute(
                text(
                    "SELECT entry_type, billed_minutes, duration_sec, amount_usd"
                    " FROM wallet_ledger WHERE call_id = :id"
                ),
                {"id": str(call_id)},
            )
        ).one()
    assert row[0] == "debit_call"
    assert row[1] == 2
    assert row[2] == 61
    assert float(row[3]) == -0.31


async def test_settle_call_is_idempotent(db: SessionFactory) -> None:
    client_id, agent_id = await seed_client(db, name="sylvastar", balance=100)
    call_id = await _insert_call(db, client_id=client_id, agent_id=agent_id, duration_sec=120)
    async with db.worker_session() as s:
        first = await wallet.settle_call(s, call_id=call_id)
    async with db.worker_session() as s:
        second = await wallet.settle_call(s, call_id=call_id)
    assert first is not None and float(first) == -0.60  # 120s x 0.30/60
    assert second is None
    async with db.console_session() as s:
        # Debited exactly once: 100 - 0.60
        assert float(await wallet.balance(s, client_account_id=client_id)) == 99.40


async def test_zero_duration_bills_nothing(db: SessionFactory) -> None:
    client_id, agent_id = await seed_client(db, name="sylvastar", balance=100)
    call_id = await _insert_call(db, client_id=client_id, agent_id=agent_id, duration_sec=0)
    async with db.worker_session() as s:
        assert await wallet.settle_call(s, call_id=call_id) is None
    async with db.console_session() as s:
        n = (
            await s.execute(
                text("SELECT count(*) FROM wallet_ledger WHERE call_id = :id"), {"id": str(call_id)}
            )
        ).scalar_one()
        assert int(n) == 0
        assert float(await wallet.balance(s, client_account_id=client_id)) == 100.0


async def test_settle_uses_claim_time_rate_snapshot(db: SessionFactory) -> None:
    client_id, agent_id = await seed_client(db, name="sylvastar", balance=100)
    call_id = await _insert_call(
        db, client_id=client_id, agent_id=agent_id, duration_sec=60, rate=0.30
    )
    async with db.console_session() as s:
        await wallet.set_rate(s, client_account_id=client_id, rate_per_min_usd=Decimal("0.50"))
    async with db.worker_session() as s:
        amount = await wallet.settle_call(s, call_id=call_id)
    # The call dialled under 0.30; the mid-flight rate change must not touch it.
    assert amount is not None and float(amount) == -0.30


async def test_negative_balance_is_allowed(db: SessionFactory) -> None:
    client_id, agent_id = await seed_client(db, name="sylvastar", balance=0.30)
    # 10 min x 0.30 = 3.00 debited against a 0.30 balance -> -2.70. The
    # ledger records what happened; blocking is the gates' job.
    call_id = await _insert_call(db, client_id=client_id, agent_id=agent_id, duration_sec=600)
    async with db.worker_session() as s:
        await wallet.settle_call(s, call_id=call_id)
    async with db.console_session() as s:
        assert float(await wallet.balance(s, client_account_id=client_id)) == -2.70
        assert await wallet.verify(s, client_account_id=client_id)


async def test_adjustment_moves_cache_both_ways(db: SessionFactory) -> None:
    client_id, _ = await seed_client(db, name="sylvastar", balance=50)
    async with db.console_session() as s:
        entry = await wallet.adjust(
            s,
            client_account_id=client_id,
            amount_usd=Decimal("-20.00"),
            staff_id=uuid.UUID(NIL),
            note="mistyped top-up, was 30 not 50",
        )
    assert entry is not None
    async with db.console_session() as s:
        assert float(await wallet.balance(s, client_account_id=client_id)) == 30.0
        assert await wallet.verify(s, client_account_id=client_id)


async def test_ledger_is_append_only(db: SessionFactory) -> None:
    await seed_client(db, name="sylvastar", balance=50)
    # The trigger RAISES (unlike audit_log's silent rules — recorded in
    # migration 0011): a mutation attempt on money history is a bug.
    with pytest.raises(DBAPIError):
        async with db.console_session() as s:
            await s.execute(text("UPDATE wallet_ledger SET amount_usd = 999"))
    with pytest.raises(DBAPIError):
        async with db.console_session() as s:
            await s.execute(text("DELETE FROM wallet_ledger"))
    async with db.console_session() as s:
        row = (await s.execute(text("SELECT count(*), max(amount_usd) FROM wallet_ledger"))).one()
    assert int(row[0]) == 1
    assert float(row[1]) == 50.0


async def test_rls_scopes_ledger_to_client(db: SessionFactory) -> None:
    a_id, _ = await seed_client(db, name="alpha", balance=10)
    b_id, _ = await seed_client(db, name="beta", balance=20)
    async with db.client_session(a_id) as s:
        rows = await wallet.ledger_page(s, client_account_id=a_id)
        cross = await wallet.ledger_page(s, client_account_id=b_id)
    assert len(rows) == 1
    assert cross == []  # RLS, not the WHERE clause, is doing the hiding


async def test_reserved_counts_calls_and_test_runs(db: SessionFactory) -> None:
    client_id, agent_id = await seed_client(db, name="sylvastar", balance=100)
    await _insert_call(db, client_id=client_id, agent_id=agent_id, status="dialling", rate=0.30)
    async with db.console_session() as s:
        version_id = (
            await s.execute(
                text(
                    "INSERT INTO agent_version (agent_id, client_account_id, n,"
                    " fields, script_blocks) VALUES (:aid, :cid, 1, '[]', '[]')"
                    " RETURNING id"
                ),
                {"aid": str(agent_id), "cid": str(client_id)},
            )
        ).scalar_one()
        await s.execute(
            text(
                "INSERT INTO test_run (agent_id, client_account_id, agent_version_id, n,"
                " schema_snapshot, stand_ins, to_number, status, rate_per_min_usd)"
                " VALUES (:aid, :cid, :vid, 1, '{}', '{}', '+2348012345678',"
                " 'dialling', 0.50)"
            ),
            {"aid": str(agent_id), "cid": str(client_id), "vid": str(version_id)},
        )
    async with db.console_session() as s:
        held = await wallet.reserved(s, client_account_id=client_id, max_call_minutes=15)
        avail = await wallet.available(s, client_account_id=client_id, max_call_minutes=15)
    # One call at 0.30 and one test run at 0.50, 15 min each:
    # (0.30 + 0.50) x 15 = 12.00 held; 100 - 12 = 88 available.
    assert float(held) == 12.0
    assert float(avail) == 88.0


async def test_set_rate_reports_actual_change(db: SessionFactory) -> None:
    client_id, _ = await seed_client(db, name="sylvastar")
    async with db.console_session() as s:
        first = await wallet.set_rate(
            s, client_account_id=client_id, rate_per_min_usd=Decimal("0.40")
        )
        again = await wallet.set_rate(
            s, client_account_id=client_id, rate_per_min_usd=Decimal("0.40")
        )
        missing = await wallet.set_rate(
            s, client_account_id=uuid.uuid4(), rate_per_min_usd=Decimal("0.40")
        )
    assert first == ("sylvastar", True)
    assert again == ("sylvastar", False)
    assert missing is None


def test_call_charge_is_per_second() -> None:
    # 1s at 0.20 rounds to nothing (and a zero debit is forbidden anyway);
    # 30s at 0.20 = 0.10; 61s at 0.30 = 0.305 -> 0.31; 600s at 0.30 = 3.00.
    assert wallet.call_charge(None, Decimal("0.20")) == Decimal("0.00")
    assert wallet.call_charge(0, Decimal("0.20")) == Decimal("0.00")
    assert wallet.call_charge(1, Decimal("0.20")) == Decimal("0.00")
    assert wallet.call_charge(30, Decimal("0.20")) == Decimal("0.10")
    assert wallet.call_charge(61, Decimal("0.30")) == Decimal("0.31")
    assert wallet.call_charge(600, Decimal("0.30")) == Decimal("3.00")


def test_billed_minutes_rounding() -> None:
    # 0 -> 0 (no conversation, no charge); 1s -> 1; 60 -> 1; 61 -> 2.
    assert wallet.billed_minutes(None) == 0
    assert wallet.billed_minutes(0) == 0
    assert wallet.billed_minutes(1) == 1
    assert wallet.billed_minutes(60) == 1
    assert wallet.billed_minutes(61) == 2
    assert wallet.billed_minutes(600) == 10
