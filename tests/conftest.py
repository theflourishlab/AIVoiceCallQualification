import os
import subprocess
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text

# Tests own their database. Set BEFORE anything calls load_settings():
# the suite deletes every row per test, and pointing it at the dev
# database once cost a full re-seed.
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://becca_app:becca@localhost:5433/becca_test"
)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

from becca.db.session import SessionFactory, make_engine  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _migrated() -> None:
    subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        check=True,
        capture_output=True,
        env={**os.environ, "DATABASE_URL": TEST_DATABASE_URL},
    )


@pytest.fixture
async def db() -> AsyncIterator[SessionFactory]:
    """A clean database per test, via the console plane (which may delete).

    Shared by tests/db and tests/web; both run against the local Docker
    Postgres (or the CI service container).
    """
    factory = SessionFactory(make_engine())
    async with factory.console_session() as s:
        # Append-only rules no-op DELETE; TRUNCATE is the test-only
        # escape hatch. wallet_ledger references call/test_run/client
        # rows, so it must clear before the DELETE list below.
        await s.execute(text("TRUNCATE wallet_ledger"))
        for t in [
            "test_run",
            "insight_result",
            "transcript",
            "saved_view",
            "call",
            "queue_item",
            "run_schedule",
            "contact",
            "contact_list",
            "notification_read",
            "notification_pref",
            "notification",  # references agent — must go before it
            "agent_version",
            "agent",
            "app_user",
            "webhook_event",
            "phone_number",
            "balance_snapshot",
            "telnyx_account_note",
            "invoice",
            "client_account",
            "becca_staff",
        ]:
            await s.execute(text(f"DELETE FROM {t}"))
        # The append-only rules no-op DELETE; TRUNCATE (owner privilege,
        # not subject to rules) is the deliberate test-only escape hatch.
        await s.execute(text("TRUNCATE audit_log"))
    yield factory
    await factory.dispose()
