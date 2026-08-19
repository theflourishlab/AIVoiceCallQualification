"""The RLS choke point (SD-10, SD-11).

Every transaction runs under exactly one of three contexts, set via
`set_config(..., true)` — the bind-parameter-friendly spelling of
SET LOCAL, so it dies with the transaction:

- client_session(client_id): app.plane='client' + app.client_id —
  policies restrict every tenant-scoped table to that client
- console_session(): app.plane='console' — Becca staff span all clients
- worker_session(): app.plane='worker' — the dispatcher spans all clients

Fail-closed: a session that sets nothing matches no policy branch and
sees zero rows. A forgotten WHERE clause cannot leak (SD-10).
"""

import uuid
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from becca.config import load_settings


def make_engine(database_url: str | None = None) -> AsyncEngine:
    settings = load_settings()
    kwargs: dict[str, object] = {}
    if settings.database_pool == "null":
        # Tests drive one app from several event loops (one per
        # TestClient); pooled asyncpg connections cannot cross loops.
        kwargs["poolclass"] = NullPool
    return create_async_engine(database_url or settings.database_url, **kwargs)


class SessionFactory:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._maker = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def _session(
        self, plane: str, client_id: uuid.UUID | None
    ) -> AsyncIterator[AsyncSession]:
        async with self._maker() as session, session.begin():
            await session.execute(
                text("SELECT set_config('app.plane', :plane, true)"),
                {"plane": plane},
            )
            if client_id is not None:
                await session.execute(
                    text("SELECT set_config('app.client_id', :cid, true)"),
                    {"cid": str(client_id)},
                )
            yield session

    def client_session(self, client_id: uuid.UUID) -> AbstractAsyncContextManager[AsyncSession]:
        return self._session("client", client_id)

    def console_session(self) -> AbstractAsyncContextManager[AsyncSession]:
        return self._session("console", None)

    def worker_session(self) -> AbstractAsyncContextManager[AsyncSession]:
        return self._session("worker", None)

    @property
    def engine(self) -> AsyncEngine:
        """For the one consumer that needs a raw connection: the SSE
        monitor's LISTEN (SD-09 — Postgres is the pub/sub)."""
        return self._engine

    async def dispose(self) -> None:
        await self._engine.dispose()
