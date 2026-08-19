"""Agent CRUD and versioning (FR-AGENT-8): editing appends versions,
never mutates one."""

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from becca.domain.codec import content_from_json, content_to_json
from becca.domain.model import AgentVersionContent


class AgentFrozen(Exception):
    """FR-LAUNCH-7: launching freezes the agent for the life of the run
    and beyond — it cannot be edited or re-scored afterwards. To change
    it, the user duplicates the agent."""


async def create_agent(
    session: AsyncSession,
    *,
    client_account_id: uuid.UUID,
    name: str,
    content: AgentVersionContent,
    brief: str = "",
) -> uuid.UUID:
    agent_id = (
        await session.execute(
            text(
                "INSERT INTO agent (client_account_id, name, brief)"
                " VALUES (:cid, :name, :brief) RETURNING id"
            ),
            {"cid": str(client_account_id), "name": name, "brief": brief},
        )
    ).scalar_one()
    await _insert_version(session, agent_id, client_account_id, 1, content)
    return uuid.UUID(str(agent_id))


async def delete_agent(session: AsyncSession, agent_id: uuid.UUID) -> dict[str, Any]:
    """Hard-delete a NEVER-LAUNCHED agent and everything it owns.

    Launched agents refuse (AgentFrozen): their calls, transcripts and
    insight results are billing and results ground truth — FR-LAUNCH-7's
    freeze holds 'for the life of the run and beyond'. For draft/tested
    agents the owned rows are versions, test runs, contact lists (with
    their contacts), saved views and notifications; run tables cannot
    have rows because launching is what changes the status.

    Returns the Telnyx scratch ids so the caller can clean the provider
    side — the row that would let the sweeper find them is gone.
    """
    row = (
        await session.execute(
            text(
                "SELECT status, telnyx_scratch_assistant_id, telnyx_scratch_texml_app_id"
                " FROM agent WHERE id = :aid"
            ),
            {"aid": str(agent_id)},
        )
    ).one()
    if row[0] not in ("draft", "tested"):
        raise AgentFrozen(str(agent_id))
    aid = {"aid": str(agent_id)}
    # Reconciliation record BEFORE the rows die (§11a: every Telnyx
    # dollar must stay explainable): the caller writes these into the
    # audit log, which has no FK and survives, so a detail record whose
    # session matches nothing still traces to "deleted agent's test N".
    test_calls = [
        {"n": r[0], "call_sid": r[1], "to": r[2], "at": r[3].isoformat()}
        for r in await session.execute(
            text(
                "SELECT n, telnyx_call_sid, to_number, created_at"
                " FROM test_run WHERE agent_id = :aid ORDER BY n"
            ),
            aid,
        )
    ]
    await session.execute(
        text(
            "DELETE FROM contact WHERE contact_list_id IN"
            " (SELECT id FROM contact_list WHERE agent_id = :aid)"
        ),
        aid,
    )
    for table in ("contact_list", "test_run", "saved_view", "notification"):
        await session.execute(
            text(f"DELETE FROM {table} WHERE agent_id = :aid"),  # noqa: S608 — our literals
            aid,
        )
    await session.execute(text("UPDATE agent SET current_version_id = NULL WHERE id = :aid"), aid)
    await session.execute(text("DELETE FROM agent_version WHERE agent_id = :aid"), aid)
    await session.execute(text("DELETE FROM agent WHERE id = :aid"), aid)
    return {
        "scratch_assistant_id": row[1],
        "scratch_texml_app_id": row[2],
        "test_calls": test_calls,
    }


async def save_new_version(
    session: AsyncSession,
    *,
    agent_id: uuid.UUID,
    client_account_id: uuid.UUID,
    content: AgentVersionContent,
) -> int:
    status = (
        await session.execute(
            text("SELECT status FROM agent WHERE id = :aid"), {"aid": str(agent_id)}
        )
    ).scalar_one()
    if status not in ("draft", "tested"):
        raise AgentFrozen(str(agent_id))
    n = (
        await session.execute(
            text("SELECT coalesce(max(n), 0) + 1 FROM agent_version WHERE agent_id = :aid"),
            {"aid": str(agent_id)},
        )
    ).scalar_one()
    await _insert_version(session, agent_id, client_account_id, int(n), content)
    return int(n)


async def _insert_version(
    session: AsyncSession,
    agent_id: Any,
    client_account_id: uuid.UUID,
    n: int,
    content: AgentVersionContent,
) -> None:
    data = content_to_json(content)
    version_id = (
        await session.execute(
            text(
                "INSERT INTO agent_version (agent_id, client_account_id, n, fields,"
                " script_blocks) VALUES (:aid, :cid, :n, :fields, :blocks) RETURNING id"
            ),
            {
                "aid": str(agent_id),
                "cid": str(client_account_id),
                "n": n,
                "fields": _json(data["fields"]),
                "blocks": _json(data["script_blocks"]),
            },
        )
    ).scalar_one()
    await session.execute(
        text("UPDATE agent SET current_version_id = :vid WHERE id = :aid"),
        {"vid": str(version_id), "aid": str(agent_id)},
    )


async def get_agent(session: AsyncSession, agent_id: uuid.UUID) -> dict[str, Any] | None:
    row = (
        await session.execute(
            text(
                "SELECT a.id, a.name, a.status, v.n, v.fields, v.script_blocks, v.id,"
                " a.voice_config, a.brief"
                " FROM agent a JOIN agent_version v ON v.id = a.current_version_id"
                " WHERE a.id = :aid"
            ),
            {"aid": str(agent_id)},
        )
    ).first()
    if row is None:
        return None
    import json as _jsonlib

    fields = row[4] if isinstance(row[4], list) else _jsonlib.loads(row[4])
    blocks = row[5] if isinstance(row[5], list) else _jsonlib.loads(row[5])
    voice_config = row[7] if isinstance(row[7], dict) else _jsonlib.loads(row[7] or "{}")
    return {
        "id": row[0],
        "name": row[1],
        "status": row[2],
        "version_n": row[3],
        "version_id": row[6],
        "content": content_from_json({"fields": fields, "script_blocks": blocks}),
        "voice_config": voice_config,
        "brief": row[8] or "",
    }


async def list_agents(session: AsyncSession) -> list[dict[str, Any]]:
    rows = await session.execute(
        text(
            "SELECT a.id, a.name, a.status, coalesce(v.n, 0), a.created_at"
            " FROM agent a LEFT JOIN agent_version v ON v.id = a.current_version_id"
            " ORDER BY a.created_at DESC"
        )
    )
    return [
        {"id": r[0], "name": r[1], "status": r[2], "version_n": r[3], "created_at": r[4]}
        for r in rows
    ]


def _json(value: Any) -> str:
    import json as _jsonlib

    return _jsonlib.dumps(value)
