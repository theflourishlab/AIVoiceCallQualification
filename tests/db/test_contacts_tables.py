"""Migration 0003's guarantees: the dedupe constraint and tenant
isolation on the contact tables."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from becca.db.session import SessionFactory
from tests.db.conftest import seed_client


async def _seed_list(
    factory: SessionFactory, client_id: uuid.UUID, agent_id: uuid.UUID, filename: str
) -> uuid.UUID:
    async with factory.client_session(client_id) as s:
        list_id = (
            await s.execute(
                text(
                    "INSERT INTO contact_list (client_account_id, agent_id, filename,"
                    " row_count, source_file) VALUES (:cid, :aid, :fn, 1, :f) RETURNING id"
                ),
                {"cid": str(client_id), "aid": str(agent_id), "fn": filename, "f": b"x"},
            )
        ).scalar_one()
        await s.execute(
            text(
                "INSERT INTO contact (contact_list_id, client_account_id, row_index,"
                " phone_raw, phone_e164, dedupe_key, diallable)"
                " VALUES (:lid, :cid, 1, '0803', '+2348030001188', 'key-1', true)"
            ),
            {"lid": str(list_id), "cid": str(client_id)},
        )
    return uuid.UUID(str(list_id))


async def test_same_dedupe_key_in_one_list_is_refused(db: SessionFactory) -> None:
    client_id, agent_id = await seed_client(db, name="sylvastar")
    list_id = await _seed_list(db, client_id, agent_id, "visits.csv")
    with pytest.raises(IntegrityError):
        async with db.client_session(client_id) as s:
            await s.execute(
                text(
                    "INSERT INTO contact (contact_list_id, client_account_id, row_index,"
                    " phone_raw, dedupe_key, diallable)"
                    " VALUES (:lid, :cid, 2, '0803', 'key-1', true)"
                ),
                {"lid": str(list_id), "cid": str(client_id)},
            )


async def test_same_dedupe_key_in_another_list_is_fine(db: SessionFactory) -> None:
    """The constraint is per list — a re-import is a new list."""
    client_id, agent_id = await seed_client(db, name="sylvastar")
    await _seed_list(db, client_id, agent_id, "visits.csv")
    await _seed_list(db, client_id, agent_id, "visits-again.csv")
    async with db.client_session(client_id) as s:
        count = (await s.execute(text("SELECT count(*) FROM contact"))).scalar_one()
        assert count == 2


async def test_contact_tables_are_tenant_isolated(db: SessionFactory) -> None:
    client_a, agent_a = await seed_client(db, name="sylvastar")
    client_b, agent_b = await seed_client(db, name="lekki-gardens")
    await _seed_list(db, client_a, agent_a, "sylvastar.csv")
    await _seed_list(db, client_b, agent_b, "lekki.csv")

    async with db.client_session(client_a) as s:
        names = (await s.execute(text("SELECT filename FROM contact_list"))).scalars().all()
        assert names == ["sylvastar.csv"]
        count = (await s.execute(text("SELECT count(*) FROM contact"))).scalar_one()
        assert count == 1

    async with db._maker() as s, s.begin():  # no GUC set: fail closed
        for table in ["contact_list", "contact"]:
            count = (await s.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()
            assert count == 0, f"{table} leaked rows to an unconfigured session"
