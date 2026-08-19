"""RLS is structural, not disciplined (SD-10). These tests are the proof.

They run as becca_app, a non-superuser owner subject to FORCE ROW LEVEL
SECURITY — migration 0001 refuses to run as a superuser precisely so
this suite cannot silently test nothing.
"""

from sqlalchemy import text

from becca.db.session import SessionFactory
from tests.db.conftest import seed_client


async def test_two_client_sessions_are_mutually_blind(db: SessionFactory) -> None:
    client_a, _ = await seed_client(db, name="sylvastar")
    client_b, _ = await seed_client(db, name="lekki-gardens")

    async with db.client_session(client_a) as s:
        agents = (await s.execute(text("SELECT name FROM agent"))).scalars().all()
        assert agents == ["sylvastar-agent"]
        accounts = (await s.execute(text("SELECT name FROM client_account"))).scalars().all()
        # FR-ARCH: must never reveal that another client exists.
        assert accounts == ["sylvastar"]

    async with db.client_session(client_b) as s:
        agents = (await s.execute(text("SELECT name FROM agent"))).scalars().all()
        assert agents == ["lekki-gardens-agent"]


async def test_forgotten_where_clause_leaks_nothing(db: SessionFactory) -> None:
    client_a, _ = await seed_client(db, name="sylvastar")
    await seed_client(db, name="lekki-gardens")
    async with db.client_session(client_a) as s:
        # No WHERE at all: RLS is the only thing scoping this query.
        count = (await s.execute(text("SELECT count(*) FROM agent"))).scalar_one()
        assert count == 1


async def test_session_with_no_guc_sees_zero_rows(db: SessionFactory) -> None:
    """Fail closed: no plane, no client id -> no policy branch matches."""
    await seed_client(db, name="sylvastar")
    async with db._maker() as s, s.begin():  # deliberately no set_config
        for table in ["client_account", "agent", "becca_staff", "webhook_event"]:
            count = (await s.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()
            assert count == 0, f"{table} leaked rows to an unconfigured session"


async def test_client_plane_cannot_read_staff_or_webhooks(db: SessionFactory) -> None:
    client_a, _ = await seed_client(db, name="sylvastar")
    async with db.console_session() as s:
        await s.execute(
            text("INSERT INTO becca_staff (google_email) VALUES ('flourish@becca.live')")
        )
        await s.execute(
            text(
                "INSERT INTO webhook_event (telnyx_event_id, event_type, payload)"
                " VALUES ('evt-1', 'test', '{}')"
            )
        )
    async with db.client_session(client_a) as s:
        assert (await s.execute(text("SELECT count(*) FROM becca_staff"))).scalar_one() == 0
        assert (await s.execute(text("SELECT count(*) FROM webhook_event"))).scalar_one() == 0


async def test_console_session_spans_all_clients(db: SessionFactory) -> None:
    await seed_client(db, name="sylvastar")
    await seed_client(db, name="lekki-gardens")
    async with db.console_session() as s:
        count = (await s.execute(text("SELECT count(*) FROM client_account"))).scalar_one()
        assert count == 2


async def test_audit_log_is_append_only(db: SessionFactory) -> None:
    client_a, _ = await seed_client(db, name="sylvastar")
    async with db.console_session() as s:
        await s.execute(
            text(
                "INSERT INTO audit_log (actor_type, actor_id, client_account_id, action)"
                " VALUES ('staff', gen_random_uuid(), :cid, 'entered_account')"
            ),
            {"cid": str(client_a)},
        )
    async with db.console_session() as s:
        await s.execute(text("UPDATE audit_log SET action = 'tampered'"))
        await s.execute(text("DELETE FROM audit_log"))
    async with db.console_session() as s:
        rows = (await s.execute(text("SELECT action FROM audit_log"))).scalars().all()
        assert rows == ["entered_account"]  # both writes were no-ops
