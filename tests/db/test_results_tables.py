"""Migration 0005's guarantees: isolation, and corrections that never
overwrite (FR-RESULT-7)."""

import uuid

from sqlalchemy import text

from becca.db.session import SessionFactory
from becca.services.results import correct_value
from tests.db.conftest import seed_client


async def _seed_result(
    db: SessionFactory, client_id: uuid.UUID, agent_id: uuid.UUID, value: str
) -> uuid.UUID:
    async with db.console_session() as s:
        call_id = (
            await s.execute(
                text(
                    "INSERT INTO call (agent_id, client_account_id, status, idempotency_key)"
                    " VALUES (:aid, :cid, 'completed', :key) RETURNING id"
                ),
                {"aid": str(agent_id), "cid": str(client_id), "key": f"k-{value}"},
            )
        ).scalar_one()
        await s.execute(
            text(
                "INSERT INTO transcript (call_id, client_account_id, agent_id, messages)"
                " VALUES (:call, :cid, :aid, '[]')"
            ),
            {"call": str(call_id), "cid": str(client_id), "aid": str(agent_id)},
        )
        await s.execute(
            text(
                "INSERT INTO insight_result (call_id, client_account_id, agent_id,"
                " field_id, field_key, value) VALUES (:call, :cid, :aid, 3, 'outcome', :val)"
            ),
            {"call": str(call_id), "cid": str(client_id), "aid": str(agent_id), "val": value},
        )
        await s.execute(
            text(
                "INSERT INTO saved_view (agent_id, client_account_id, name, filters)"
                " VALUES (:aid, :cid, 'Qualified', '{}')"
            ),
            {"aid": str(agent_id), "cid": str(client_id)},
        )
    return uuid.UUID(str(call_id))


async def test_results_tables_are_tenant_isolated(db: SessionFactory) -> None:
    client_a, agent_a = await seed_client(db, name="sylvastar")
    client_b, agent_b = await seed_client(db, name="lekki-gardens")
    await _seed_result(db, client_a, agent_a, "a-value")
    await _seed_result(db, client_b, agent_b, "b-value")

    async with db.client_session(client_a) as s:
        values = (await s.execute(text("SELECT value FROM insight_result"))).scalars().all()
        assert values == ["a-value"]
        for table in ["transcript", "saved_view"]:
            n = (await s.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()
            assert int(n) == 1, table

    async with db._maker() as s, s.begin():  # no GUC: fail closed
        for table in ["transcript", "insight_result", "saved_view"]:
            n = (await s.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()
            assert int(n) == 0, f"{table} leaked"


async def test_correction_is_stored_alongside_the_original(db: SessionFactory) -> None:
    client_id, agent_id = await seed_client(db, name="sylvastar")
    call_id = await _seed_result(db, client_id, agent_id, "unsure")

    async with db.client_session(client_id) as s:
        await correct_value(
            s, call_id=call_id, field_id=3, corrected_value="yes", corrected_by=uuid.uuid4()
        )
    async with db.client_session(client_id) as s:
        value, corrected, corrected_at = (
            await s.execute(
                text(
                    "SELECT value, corrected_value, corrected_at FROM insight_result"
                    " WHERE call_id = :call"
                ),
                {"call": str(call_id)},
            )
        ).one()
    assert value == "unsure"  # the original survives (FR-RESULT-7)
    assert corrected == "yes"
    assert corrected_at is not None
