"""The concurrency governor (FR-DISPATCH-9): correct under concurrency
because it is a count behind a per-client row lock, not process memory."""

import asyncio
import uuid

from sqlalchemy import text

from becca.db.session import SessionFactory
from becca.worker.claims import Claim, claim_one
from tests.db.conftest import seed_client


async def _enqueue(db: SessionFactory, client_id: uuid.UUID, agent_id: uuid.UUID, n: int) -> None:
    async with db.console_session() as s:
        for _ in range(n):
            await s.execute(
                text(
                    "INSERT INTO queue_item (agent_id, client_account_id, idempotency_key)"
                    " VALUES (:aid, :cid, :key)"
                ),
                {"aid": str(agent_id), "cid": str(client_id), "key": f"qi-{uuid.uuid4()}"},
            )


async def _try_claim(db: SessionFactory, client_id: uuid.UUID, agent_id: uuid.UUID) -> Claim | None:
    async with db.worker_session() as s:
        return await claim_one(
            s, client_account_id=client_id, agent_id=agent_id, max_call_minutes=15
        )


async def test_concurrent_claims_respect_allocation_one(db: SessionFactory) -> None:
    client_id, agent_id = await seed_client(db, name="sylvastar", allocation=1)
    await _enqueue(db, client_id, agent_id, 2)

    results = await asyncio.gather(
        _try_claim(db, client_id, agent_id),
        _try_claim(db, client_id, agent_id),
    )
    assert sum(1 for r in results if r is not None) == 1


async def test_slot_frees_when_call_leaves_dialling(db: SessionFactory) -> None:
    client_id, agent_id = await seed_client(db, name="sylvastar", allocation=1)
    await _enqueue(db, client_id, agent_id, 2)

    first = await _try_claim(db, client_id, agent_id)
    assert first is not None
    assert await _try_claim(db, client_id, agent_id) is None  # allocation full

    async with db.worker_session() as s:
        await s.execute(
            text("UPDATE call SET status = 'completed' WHERE id = :id"),
            {"id": str(first.call_id)},
        )
    second = await _try_claim(db, client_id, agent_id)
    assert second is not None
    assert second.queue_item_id != first.queue_item_id


async def test_clients_do_not_starve_each_other(db: SessionFactory) -> None:
    a_client, a_agent = await seed_client(db, name="sylvastar", allocation=1)
    b_client, b_agent = await seed_client(db, name="lekki-gardens", allocation=1)
    await _enqueue(db, a_client, a_agent, 1)
    await _enqueue(db, b_client, b_agent, 1)

    assert await _try_claim(db, a_client, a_agent) is not None
    # A's slot being full must not affect B (FR-DISPATCH-9).
    assert await _try_claim(db, b_client, b_agent) is not None


async def test_empty_queue_returns_none(db: SessionFactory) -> None:
    client_id, agent_id = await seed_client(db, name="sylvastar", allocation=4)
    assert await _try_claim(db, client_id, agent_id) is None


async def test_claim_refused_when_wallet_cannot_cover_hold(db: SessionFactory) -> None:
    # A hold is rate x 15 min = 0.30 x 15 = 4.50; a 4.49 wallet cannot cover it.
    client_id, agent_id = await seed_client(db, name="sylvastar", allocation=4, balance=4.49)
    await _enqueue(db, client_id, agent_id, 1)
    assert await _try_claim(db, client_id, agent_id) is None


async def test_claim_granted_at_exactly_one_hold(db: SessionFactory) -> None:
    # 4.50 covers exactly one 4.50 hold; the second claim must refuse.
    client_id, agent_id = await seed_client(db, name="sylvastar", allocation=4, balance=4.50)
    await _enqueue(db, client_id, agent_id, 2)
    first = await _try_claim(db, client_id, agent_id)
    assert first is not None
    assert await _try_claim(db, client_id, agent_id) is None


async def test_claim_snapshots_rate_onto_call(db: SessionFactory) -> None:
    client_id, agent_id = await seed_client(db, name="sylvastar", rate_per_min=0.42)
    await _enqueue(db, client_id, agent_id, 1)
    claim = await _try_claim(db, client_id, agent_id)
    assert claim is not None
    async with db.console_session() as s:
        rate = (
            await s.execute(
                text("SELECT rate_per_min_usd FROM call WHERE id = :id"),
                {"id": str(claim.call_id)},
            )
        ).scalar_one()
    assert float(rate) == 0.42


async def test_dialling_test_run_consumes_wallet_cover(db: SessionFactory) -> None:
    # Balance 9.00 covers two 4.50 holds. A dialling test run takes one;
    # only one run claim fits after it.
    client_id, agent_id = await seed_client(db, name="sylvastar", allocation=4, balance=9.00)
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
                " 'dialling', 0.30)"
            ),
            {"aid": str(agent_id), "cid": str(client_id), "vid": str(version_id)},
        )
    await _enqueue(db, client_id, agent_id, 2)
    assert await _try_claim(db, client_id, agent_id) is not None
    assert await _try_claim(db, client_id, agent_id) is None
