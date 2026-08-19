"""Phase 6 console state: zero-sum allocation, number assignment rules,
and RLS on the new tables (FR-CONSOLE-3/5, SD-10)."""

import uuid

import pytest
from sqlalchemy import text

from becca.db.session import SessionFactory
from becca.services import console as console_service
from becca.services.console import AllocationExceeded, ReassignmentRefused
from tests.db.conftest import seed_client

CEILING = 10


async def _seed_number(
    factory: SessionFactory, *, e164: str, client_id: uuid.UUID | None = None
) -> uuid.UUID:
    async with factory.console_session() as s:
        number_id = (
            await s.execute(
                text(
                    "INSERT INTO phone_number (phone_e164, client_account_id, assigned_at)"
                    " VALUES (:e, cast(:cid AS uuid),"
                    "         CASE WHEN cast(:cid AS uuid) IS NULL THEN NULL ELSE now() END)"
                    " RETURNING id"
                ),
                {"e": e164, "cid": str(client_id) if client_id else None},
            )
        ).scalar_one()
    return uuid.UUID(str(number_id))


async def test_allocation_is_zero_sum_against_the_ceiling(db: SessionFactory) -> None:
    await seed_client(db, name="sylvastar", allocation=6)
    client_b, _ = await seed_client(db, name="lekki", allocation=0)

    # 4 remaining: fits exactly.
    async with db.console_session() as s:
        await console_service.set_allocation(
            s, client_account_id=client_b, channels=4, ceiling=CEILING
        )
    # 5 would oversubscribe: refused, and the stored value is untouched.
    with pytest.raises(AllocationExceeded):
        async with db.console_session() as s:
            await console_service.set_allocation(
                s, client_account_id=client_b, channels=7, ceiling=CEILING
            )
    async with db.console_session() as s:
        split = await console_service.allocation_split(s, ceiling=CEILING)
        assert split["allocated"] == 10
        assert split["unallocated"] == 0
        by_name = {c["name"]: c["allocation"] for c in split["clients"]}
        assert by_name == {"sylvastar": 6, "lekki": 4}


async def test_negative_allocation_is_refused(db: SessionFactory) -> None:
    client_a, _ = await seed_client(db, name="sylvastar")
    with pytest.raises(AllocationExceeded):
        async with db.console_session() as s:
            await console_service.set_allocation(
                s, client_account_id=client_a, channels=-1, ceiling=CEILING
            )


async def test_reassignment_refused_while_dialling(db: SessionFactory) -> None:
    """FR-CONSOLE-5: the number is a dialling run's caller ID."""
    client_a, agent_a = await seed_client(db, name="sylvastar")
    client_b, _ = await seed_client(db, name="lekki")
    number_id = await _seed_number(db, e164="+2342093940544", client_id=client_a)

    async with db.console_session() as s:
        await s.execute(
            text("UPDATE agent SET status = 'dialling' WHERE id = :aid"),
            {"aid": str(agent_a)},
        )
    with pytest.raises(ReassignmentRefused):
        async with db.console_session() as s:
            await console_service.assign_number(
                s, phone_number_id=number_id, client_account_id=client_b
            )
    # Still Sylvastar's.
    async with db.console_session() as s:
        owner = (
            await s.execute(
                text("SELECT client_account_id FROM phone_number WHERE id = :nid"),
                {"nid": str(number_id)},
            )
        ).scalar_one()
        assert owner == client_a

    # Run finished -> reassignment proceeds.
    async with db.console_session() as s:
        await s.execute(
            text("UPDATE agent SET status = 'finished' WHERE id = :aid"),
            {"aid": str(agent_a)},
        )
        await console_service.assign_number(
            s, phone_number_id=number_id, client_account_id=client_b
        )
        owner = (
            await s.execute(
                text("SELECT client_account_id FROM phone_number WHERE id = :nid"),
                {"nid": str(number_id)},
            )
        ).scalar_one()
        assert owner == client_b


async def test_assigning_an_unassigned_number_never_blocks(db: SessionFactory) -> None:
    """A dialling run elsewhere must not lock the free pool."""
    client_a, agent_a = await seed_client(db, name="sylvastar")
    number_id = await _seed_number(db, e164="+2341888410001")
    async with db.console_session() as s:
        await s.execute(
            text("UPDATE agent SET status = 'dialling' WHERE id = :aid"),
            {"aid": str(agent_a)},
        )
        await console_service.assign_number(
            s, phone_number_id=number_id, client_account_id=client_a
        )


async def test_phone_number_rls(db: SessionFactory) -> None:
    """A client session sees only its own numbers; unassigned numbers and
    the console-only tables are invisible to it; no GUC sees nothing."""
    client_a, _ = await seed_client(db, name="sylvastar")
    client_b, _ = await seed_client(db, name="lekki")
    await _seed_number(db, e164="+2341000000001", client_id=client_a)
    await _seed_number(db, e164="+2341000000002", client_id=client_b)
    await _seed_number(db, e164="+2341000000003")  # unassigned

    async with db.client_session(client_a) as s:
        visible = (await s.execute(text("SELECT phone_e164 FROM phone_number"))).scalars().all()
        assert visible == ["+2341000000001"]

    async with db.console_session() as s:
        await s.execute(text("INSERT INTO balance_snapshot (available_credit) VALUES (42)"))
    async with db.client_session(client_a) as s:
        count = (await s.execute(text("SELECT count(*) FROM balance_snapshot"))).scalar_one()
        assert count == 0, "balance_snapshot leaked to a client session"

    async with db._maker() as s, s.begin():  # deliberately no set_config
        for table in ["phone_number", "balance_snapshot", "telnyx_account_note"]:
            count = (await s.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()
            assert count == 0, f"{table} leaked rows to an unconfigured session"


async def test_client_status_flips_active_when_onboarding_complete(db: SessionFactory) -> None:
    """FR-CONSOLE-4's trio: acknowledgement + channels + number."""
    client_a, _ = await seed_client(db, name="sylvastar", allocation=0)

    async with db.console_session() as s:
        await s.execute(
            text("UPDATE client_account SET acknowledged_at = now() WHERE id = :cid"),
            {"cid": str(client_a)},
        )
        await console_service.refresh_client_status(s, client_account_id=client_a)
        status = (
            await s.execute(
                text("SELECT status FROM client_account WHERE id = :cid"),
                {"cid": str(client_a)},
            )
        ).scalar_one()
        assert status == "onboarding", "flipped active without channels or a number"

    number_id = await _seed_number(db, e164="+2342093940544")
    async with db.console_session() as s:
        await console_service.set_allocation(
            s, client_account_id=client_a, channels=2, ceiling=CEILING
        )
        await console_service.assign_number(
            s, phone_number_id=number_id, client_account_id=client_a
        )
        status = (
            await s.execute(
                text("SELECT status FROM client_account WHERE id = :cid"),
                {"cid": str(client_a)},
            )
        ).scalar_one()
        assert status == "active"
