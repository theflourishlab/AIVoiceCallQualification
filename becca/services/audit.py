"""Append-only audit writes (SD-36, FR-CONSOLE-8)."""

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def record(
    session: AsyncSession,
    *,
    actor_type: str,
    actor_id: uuid.UUID,
    action: str,
    client_account_id: uuid.UUID | None = None,
    target: str | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    await session.execute(
        text(
            "INSERT INTO audit_log (actor_type, actor_id, client_account_id, action,"
            " target, metadata) VALUES (:atype, :aid, :cid, :action, :target, :meta)"
        ),
        {
            "atype": actor_type,
            "aid": str(actor_id),
            "cid": str(client_account_id) if client_account_id else None,
            "action": action,
            "target": target,
            "meta": json.dumps(meta or {}),
        },
    )
