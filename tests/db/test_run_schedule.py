"""Migration 0004: run_schedule is tenant-isolated like everything else."""

from sqlalchemy import text

from becca.db.session import SessionFactory
from tests.db.conftest import seed_client


async def _seed_schedule(db: SessionFactory, client_id: object, agent_id: object) -> None:
    async with db.console_session() as s:
        list_id = (
            await s.execute(
                text(
                    "INSERT INTO contact_list (client_account_id, agent_id, filename,"
                    " row_count, source_file) VALUES (:cid, :aid, 'l.csv', 0, :f) RETURNING id"
                ),
                {"cid": str(client_id), "aid": str(agent_id), "f": b"x"},
            )
        ).scalar_one()
        await s.execute(
            text(
                "INSERT INTO run_schedule (agent_id, client_account_id, contact_list_id,"
                " window_start, window_end, days, spend_cap)"
                " VALUES (:aid, :cid, :lid, '09:00', '17:00', :days, 100)"
            ),
            {"aid": str(agent_id), "cid": str(client_id), "lid": str(list_id), "days": [1, 2]},
        )


async def test_run_schedule_is_tenant_isolated(db: SessionFactory) -> None:
    client_a, agent_a = await seed_client(db, name="sylvastar")
    client_b, agent_b = await seed_client(db, name="lekki-gardens")
    await _seed_schedule(db, client_a, agent_a)
    await _seed_schedule(db, client_b, agent_b)

    async with db.client_session(client_a) as s:
        count = (await s.execute(text("SELECT count(*) FROM run_schedule"))).scalar_one()
        assert int(count) == 1

    async with db._maker() as s, s.begin():  # no GUC: fail closed
        count = (await s.execute(text("SELECT count(*) FROM run_schedule"))).scalar_one()
        assert int(count) == 0
