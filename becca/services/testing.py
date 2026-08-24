"""The test loop (FR-TEST-1..9): a loop, not a checkpoint.

The scratch assistant is the one mutable Telnyx object — it has exactly
one consumer (FR-TEST-3). Schema edits mutate its insight group in
place; each test stores a snapshot of the schema it used (FR-TEST-4)
because insight results carry no template revision.

Results are pulled, not pushed: the spike showed insight events are not
delivered to per-call callback URLs, and scoring completes ~2s after
hangup, so the screen polls while a test is dialling.
"""

import json
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from becca.domain.codec import content_to_json
from becca.domain.model import AgentVersionContent
from becca.domain.serialize import (
    dynamic_variable_keys,
    insight_payloads,
    natural_greeting,
    render_assistant_instructions,
)
from becca.domain.views import insight_schema
from becca.services.voice_config import EffectiveVoiceConfig
from becca.telnyx.gateway import TelnyxGateway


async def ensure_scratch_synced(
    session: AsyncSession,
    gateway: TelnyxGateway,
    *,
    agent_id: uuid.UUID,
    content: AgentVersionContent,
    voice_config: EffectiveVoiceConfig,
) -> tuple[str, str]:
    """Create the scratch assistant on first test; on later tests, mutate
    its insight group and config to match the current content (FR-TEST-3).

    Returns (scratch_assistant_id, scratch_texml_app_id).
    """
    row = (
        await session.execute(
            text(
                "SELECT telnyx_scratch_assistant_id, telnyx_scratch_texml_app_id,"
                " telnyx_insight_group_id, telnyx_scratch_insight_map, name"
                " FROM agent WHERE id = :aid"
            ),
            {"aid": str(agent_id)},
        )
    ).first()
    if row is None:
        raise LookupError(f"agent {agent_id}")
    assistant_id, texml_app_id, group_id, insight_map_raw, agent_name = row
    insight_map: dict[str, str] = (
        insight_map_raw if isinstance(insight_map_raw, dict) else json.loads(insight_map_raw)
    )

    instructions = render_assistant_instructions(content)
    greeting = natural_greeting(content)
    # Optional inputs must carry a default that reads naturally when
    # spoken (FR-CONTACT-9); stand-ins replace these at dial time.
    dynamic_variables = {k: "" for k in dynamic_variable_keys(content)}

    if assistant_id is None:
        group_id = await gateway.create_insight_group(name=f"scratch-{agent_id}")
        insight_map = {}
        for payload in insight_payloads(content):
            insight_id = await gateway.create_insight(
                name=payload["name"],
                instructions=payload["instructions"],
                json_schema=payload["json_schema"],
            )
            await gateway.assign_insight_to_group(group_id=group_id, insight_id=insight_id)
            insight_map[str(payload["field_id"])] = insight_id
        assistant = await gateway.create_assistant(
            name=f"scratch — {agent_name}",
            model=voice_config.model,
            instructions=instructions,
            greeting=greeting,
            voice=voice_config.voice,
            voice_speed=voice_config.voice_speed,
            transcription_model=voice_config.transcription_model,
            interruption=voice_config.interruption,
            time_limit_secs=voice_config.time_limit_secs,
            idle_timeout_secs=voice_config.idle_timeout_secs,
            insight_group_id=group_id,
            dynamic_variables=dynamic_variables,
        )
        assistant_id, texml_app_id = assistant.id, assistant.default_texml_app_id
    else:
        # Diff by field id: add new, update existing, unassign removed.
        current = {str(p["field_id"]): p for p in insight_payloads(content)}
        for field_id, payload in current.items():
            if field_id in insight_map:
                await gateway.update_insight(
                    insight_id=insight_map[field_id],
                    name=payload["name"],
                    instructions=payload["instructions"],
                    json_schema=payload["json_schema"],
                )
            else:
                insight_id = await gateway.create_insight(
                    name=payload["name"],
                    instructions=payload["instructions"],
                    json_schema=payload["json_schema"],
                )
                await gateway.assign_insight_to_group(group_id=group_id, insight_id=insight_id)
                insight_map[field_id] = insight_id
        for field_id in [f for f in insight_map if f not in current]:
            await gateway.unassign_insight_from_group(
                group_id=group_id, insight_id=insight_map.pop(field_id)
            )
        await gateway.update_assistant(
            assistant_id=assistant_id,
            model=voice_config.model,
            instructions=instructions,
            greeting=greeting,
            voice=voice_config.voice,
            voice_speed=voice_config.voice_speed,
            transcription_model=voice_config.transcription_model,
            interruption=voice_config.interruption,
            time_limit_secs=voice_config.time_limit_secs,
            idle_timeout_secs=voice_config.idle_timeout_secs,
            dynamic_variables=dynamic_variables,
        )

    await session.execute(
        text(
            "UPDATE agent SET telnyx_scratch_assistant_id = :sid,"
            " telnyx_scratch_texml_app_id = :tid, telnyx_insight_group_id = :gid,"
            " telnyx_scratch_insight_map = :map WHERE id = :aid"
        ),
        {
            "sid": assistant_id,
            "tid": texml_app_id,
            "gid": group_id,
            "map": json.dumps(insight_map),
            "aid": str(agent_id),
        },
    )
    return str(assistant_id), str(texml_app_id)


async def reserve_test_run(
    session: AsyncSession,
    *,
    agent_id: uuid.UUID,
    client_account_id: uuid.UUID,
    agent_version_id: uuid.UUID,
    content: AgentVersionContent,
    to_number: str,
    stand_ins: dict[str, str],
    wallet_floor_usd: float,
) -> uuid.UUID | None:
    """Reserve-then-place, the dispatcher's SD-27 shape: the row exists
    before a single packet reaches Telnyx; a crash after this commit
    leaves a 'dialling' test_run that housekeeping fails at 15 minutes.

    The gate is the balance itself (24 Aug 2026): a test call goes out
    while the wallet holds at least `wallet_floor_usd`, and settlement
    debits the per-second actual. Run dispatch still reserves worst-case
    holds (FR-WALLET-4); the row lock taken here is the same one the
    dispatcher's claims take, so the two never race the cache.

    Returns None when the balance is under the floor."""
    locked = (
        await session.execute(
            text(
                "SELECT wallet_balance_usd, rate_per_min_usd FROM client_account"
                " WHERE id = :cid FOR UPDATE"
            ),
            {"cid": str(client_account_id)},
        )
    ).first()
    if locked is None:
        return None
    if Decimal(locked[0]) < Decimal(str(wallet_floor_usd)):
        return None
    n = (
        await session.execute(
            text("SELECT coalesce(max(n), 0) + 1 FROM test_run WHERE agent_id = :aid"),
            {"aid": str(agent_id)},
        )
    ).scalar_one()
    schema_snapshot = [f for f in content_to_json(content)["fields"] if f["kind"] == "output"]
    test_run_id = (
        await session.execute(
            text(
                "INSERT INTO test_run (agent_id, client_account_id, agent_version_id, n,"
                " schema_snapshot, stand_ins, to_number, status, rate_per_min_usd)"
                " VALUES (:aid, :cid, :vid, :n, :snap, :stand, :to, 'dialling', :rate)"
                " RETURNING id"
            ),
            {
                "aid": str(agent_id),
                "cid": str(client_account_id),
                "vid": str(agent_version_id),
                "n": int(n),
                "snap": json.dumps(schema_snapshot),
                "stand": json.dumps(stand_ins),
                "to": to_number,
                "rate": locked[1],
            },
        )
    ).scalar_one()
    return uuid.UUID(str(test_run_id))


async def dial_test_run(
    session: AsyncSession,
    gateway: TelnyxGateway,
    *,
    test_run_id: uuid.UUID,
    agent_id: uuid.UUID,
    content: AgentVersionContent,
    to_number: str,
    stand_ins: dict[str, str],
    from_number: str,
    record: bool,
    voice_config: EffectiveVoiceConfig,
    webhook_base: str = "",
) -> None:
    """The dial half, in its own transaction AFTER the reservation
    committed. Gateway errors propagate and roll this back — the caller
    marks the reserved row failed in a fresh session (fail_test_run)."""
    assistant_id, texml_app_id = await ensure_scratch_synced(
        session, gateway, agent_id=agent_id, content=content, voice_config=voice_config
    )
    response = await gateway.place_call(
        connection_id=texml_app_id,
        assistant_id=assistant_id,
        to=to_number,
        from_=from_number,
        variables=stand_ins,
        # Test conversations are recognisable by the scratch assistant id
        # (FR-TEST-5's metadata mechanism is unproven — spike finding).
        metadata={"becca_agent_id": str(agent_id), "becca_kind": "test"},
        record=record,
        status_callback=f"{webhook_base}/webhooks/telnyx" if webhook_base else "",
        amd_status_callback=f"{webhook_base}/webhooks/telnyx" if webhook_base else "",
    )
    await session.execute(
        text("UPDATE test_run SET telnyx_call_sid = :sid WHERE id = :id"),
        {"sid": str(response.get("call_sid", "")), "id": str(test_run_id)},
    )


async def fail_test_run(session: AsyncSession, *, test_run_id: uuid.UUID) -> None:
    """Release a reservation whose dial never happened."""
    await session.execute(
        text("UPDATE test_run SET status = 'failed' WHERE id = :id AND status = 'dialling'"),
        {"id": str(test_run_id)},
    )


def _parse_result(raw: str) -> Any:
    """Schema results arrive as stringified JSON ('{"key": "yes"}');
    free-text as plain strings (spike finding)."""
    try:
        parsed = json.loads(raw)
    except ValueError, TypeError:
        return raw
    if isinstance(parsed, dict) and len(parsed) == 1:
        return next(iter(parsed.values()))
    return parsed


async def _fail_stale_runs(session: AsyncSession, agent_id: uuid.UUID) -> set[uuid.UUID]:
    """The two-tier timeout (see fetch_pending_results). Returns the ids
    of runs marked failed."""
    rows = (
        await session.execute(
            text(
                "UPDATE test_run SET status = 'failed'"
                " WHERE agent_id = :aid AND status = 'dialling' AND ("
                "   (telnyx_conversation_id IS NULL"
                "     AND created_at < now() - interval '90 seconds')"
                "   OR created_at < now() - interval '8 minutes')"
                " RETURNING id"
            ),
            {"aid": str(agent_id)},
        )
    ).all()
    return {r[0] for r in rows}


async def fetch_pending_results(
    session: AsyncSession, gateway: TelnyxGateway, *, agent_id: uuid.UUID
) -> bool:
    """Try to complete any dialling test runs by pulling the scratch
    assistant's conversations. Returns True if anything changed.

    Two-tier timeout, learned live: a run whose call never produced a
    conversation is dead after 90 seconds (the 480 case), but a run with
    a matched conversation may still be mid-call — a test call rings,
    talks for minutes, and is scored ~2s after hangup. Marking those
    failed at 90s killed a successful call's results.
    """
    pending = (
        await session.execute(
            text(
                "SELECT id, telnyx_conversation_id, telnyx_call_sid FROM test_run"
                " WHERE agent_id = :aid AND status = 'dialling' ORDER BY n"
            ),
            {"aid": str(agent_id)},
        )
    ).all()
    if not pending:
        return False
    row = (
        await session.execute(
            text(
                "SELECT telnyx_scratch_assistant_id, telnyx_scratch_insight_map"
                " FROM agent WHERE id = :aid"
            ),
            {"aid": str(agent_id)},
        )
    ).first()
    if row is None or row[0] is None:
        # No scratch assistant — the sweeper clears the ids once a launch
        # orphans it (FR-LAUNCH-5/6), so a run still dialling here can
        # never be matched to a conversation and there is nothing to ask
        # Telnyx about. Time it out, or it stays dialling forever and the
        # test screen auto-refreshes forever (observed live 13 Aug 2026:
        # test run 11, stranded when its agent launched mid-dial). The
        # only-after-a-successful-query rule below protects verdicts made
        # while offline; here no query can ever succeed.
        return len(await _fail_stale_runs(session, agent_id)) > 0
    assistant_id = str(row[0])
    insight_map = row[1] if isinstance(row[1], dict) else json.loads(row[1])
    key_by_insight = {v: k for k, v in insight_map.items()}

    try:
        conversations = await gateway.find_conversations(assistant_id=assistant_id)
    except Exception:
        # Telnyx unreachable (a dev machine losing its hotspot mid-call,
        # observed live): no verdicts of any kind on stale information.
        return False
    # Exact join: the conversation's metadata carries the call control id,
    # which is the call_sid the dial response gave us. Positional matching
    # (oldest pending ↔ oldest conversation) once handed a run a stale
    # conversation and displayed the previous call's results under the
    # wrong test number — never guess when an exact key exists.
    conv_by_call_sid = {
        str(c.get("metadata", {}).get("call_control_id", "")): str(c["id"])
        for c in conversations
        if isinstance(c.get("metadata"), dict)
    }
    # The timeout may only fire AFTER a successful query, so "no
    # conversation" means Telnyx really has none — not that we're offline.
    timed_out = await _fail_stale_runs(session, agent_id)
    pending = [p for p in pending if p[0] not in timed_out]
    changed = len(timed_out) > 0
    for test_run_id, matched_conversation_id, call_sid in pending:
        conversation_id = str(matched_conversation_id) if matched_conversation_id else None
        if conversation_id is None:
            conversation_id = conv_by_call_sid.get(str(call_sid or "")) or None
            if conversation_id is not None:
                # Persist the match immediately so a mid-call run is not
                # timed out as never-connected, and survives reloads.
                await session.execute(
                    text("UPDATE test_run SET telnyx_conversation_id = :conv WHERE id = :id"),
                    {"conv": conversation_id, "id": str(test_run_id)},
                )
                changed = True
        if conversation_id is None:
            continue
        insight_data = await gateway.get_conversation_insights(conversation_id=conversation_id)
        results: dict[str, Any] = {}
        completed = False
        for entry in insight_data:
            if entry.get("status") not in (None, "completed"):
                continue
            for item in entry.get("conversation_insights", []):
                field_id = key_by_insight.get(item.get("insight_id"))
                if field_id is not None:
                    results[field_id] = _parse_result(item.get("result", ""))
            completed = True
        if not completed or not results:
            continue
        messages = await gateway.get_conversation_messages(conversation_id=conversation_id)
        # Not chronological as returned (spike finding) — sort.
        messages = sorted(messages, key=lambda m: str(m.get("created_at", "")))
        await session.execute(
            text(
                "UPDATE test_run SET status = 'complete',"
                " results = :results, transcript = :transcript WHERE id = :id"
            ),
            {
                "results": json.dumps(results),
                "transcript": json.dumps(messages),
                "id": str(test_run_id),
            },
        )
        changed = True
    if changed:
        # FR-TEST-6: an agent may not launch until tested at least once.
        await session.execute(
            text("UPDATE agent SET status = 'tested' WHERE id = :aid AND status = 'draft'"),
            {"aid": str(agent_id)},
        )
    return changed


@dataclass(frozen=True)
class ResultDiff:
    key: str
    value: Any
    previous: Any
    change: str  # "new" | "changed" | "unchanged" | "gone"


def diff_results(
    content: AgentVersionContent,
    current: dict[str, Any] | None,
    previous: dict[str, Any] | None,
) -> list[ResultDiff]:
    """Which extracted fields changed since the previous test (FR-TEST-7).

    Keyed by field id (rename-proof); displayed by current key."""
    current = current or {}
    previous = previous or {}
    key_by_id = {str(f.id): f.key for f in insight_schema(content)}
    diffs: list[ResultDiff] = []
    for field_id, key in key_by_id.items():
        cur, prev = current.get(field_id), previous.get(field_id)
        if field_id not in current:
            continue
        if field_id not in previous:
            change = "new"
        elif cur != prev:
            change = "changed"
        else:
            change = "unchanged"
        diffs.append(ResultDiff(key=key, value=cur, previous=prev, change=change))
    for field_id in previous:
        if field_id not in current and field_id in key_by_id:
            diffs.append(
                ResultDiff(
                    key=key_by_id[field_id], value=None, previous=previous[field_id], change="gone"
                )
            )
    return diffs


async def list_test_runs(session: AsyncSession, agent_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = await session.execute(
        text(
            "SELECT id, n, status, results, transcript, stand_ins, to_number, created_at"
            " FROM test_run WHERE agent_id = :aid ORDER BY n DESC"
        ),
        {"aid": str(agent_id)},
    )
    out = []
    for r in rows:
        out.append(
            {
                "id": r[0],
                "n": r[1],
                "status": r[2],
                "results": r[3] if isinstance(r[3], dict) else json.loads(r[3] or "{}"),
                "transcript": r[4] if isinstance(r[4], list) else json.loads(r[4] or "[]"),
                "stand_ins": r[5] if isinstance(r[5], dict) else json.loads(r[5] or "{}"),
                "to_number": r[6],
                "created_at": r[7],
            }
        )
    return out
