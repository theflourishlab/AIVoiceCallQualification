"""Results ingest: poll-on-completed, because push does not exist.

The FRD assumed `call.conversation_insights.generated` would be
delivered to us (FR-RESULT-1); the spike proved insight events never
reach per-call callback URLs, so the worker polls: any completed call
without a transcript row gets its conversation looked up (exact join on
call_control_id in the conversation's metadata — never positional),
its insights parsed the way the test loop parses them, and both
mirrored into our database, which is the system of record from that
moment. Scoring completes ~2s after hangup (observed live), so a call
polled too early simply matches on a later tick.

Provenance (FR-RESULT-6) is computed here, by us: Telnyx links nothing
to the moment that produced it. The v1 heuristic is honest rather than
clever — a case-insensitive containment search over the contact's own
turns; a value nobody spoke gets no quote rather than a fabricated one.
"""

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from becca.db.session import SessionFactory

# The same stringified-JSON unwrapping the test loop uses (spike shape).
from becca.services.testing import _parse_result
from becca.telnyx.gateway import TelnyxGateway

# A completed call whose conversation never appears is abandoned after
# this long — failed calls briefly create conversations that Telnyx
# garbage-collects (spike finding), so absence is a real outcome.
GIVE_UP_MINUTES = 10


def _jsonish(value: Any) -> Any:
    return value if isinstance(value, dict | list) else json.loads(value)


def provenance(messages: list[dict[str, Any]], value: str) -> tuple[str | None, float | None]:
    """(quote, seconds-into-call) for the contact turn that contains the
    value, or (None, None). Only the contact's own words count as
    provenance — the agent saying a value proves nothing about the
    answer."""
    needle = str(value).strip().casefold()
    if not needle:
        return None, None
    start = _seconds(messages[0].get("created_at")) if messages else None
    for m in messages:
        if str(m.get("role", "")) != "user":
            continue
        text_ = str(m.get("text", ""))
        if needle in text_.casefold():
            at = _seconds(m.get("created_at"))
            offset = (at - start) if (at is not None and start is not None) else None
            return text_, offset
    return None, None


def _seconds(created_at: Any) -> float | None:
    """Best-effort epoch seconds from whatever timestamp shape arrives."""
    from datetime import datetime

    if created_at is None:
        return None
    try:
        return datetime.fromisoformat(str(created_at).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


async def notify_run(session: AsyncSession, agent_id: Any) -> None:
    """Postgres is the pub/sub (SD-09): the SSE monitor LISTENs on this."""
    await session.execute(text("SELECT pg_notify('run_progress', :aid)"), {"aid": str(agent_id)})


async def results_tick(db: SessionFactory, gateway: TelnyxGateway) -> int:
    """Mirror results for completed calls that lack them. Returns the
    number of calls ingested. One find_conversations per agent per tick."""
    async with db.worker_session() as s:
        candidates = (
            await s.execute(
                text(
                    """
                    SELECT c.id, c.agent_id, c.client_account_id,
                           c.telnyx_call_control_id, c.telnyx_conversation_id,
                           c.ended_at < now() - make_interval(mins => :give_up) AS stale,
                           a.telnyx_run_assistant_id, a.telnyx_run_insight_map,
                           v.fields
                      FROM call c
                      JOIN agent a ON a.id = c.agent_id
                      JOIN agent_version v ON v.id = a.current_version_id
                 LEFT JOIN transcript t ON t.call_id = c.id
                     WHERE c.status = 'completed'
                       AND t.call_id IS NULL
                       AND a.telnyx_run_assistant_id IS NOT NULL
                     ORDER BY c.ended_at
                     LIMIT 20
                    """
                ),
                {"give_up": GIVE_UP_MINUTES},
            )
        ).all()
    if not candidates:
        return 0

    conversations_by_agent: dict[str, dict[str, str]] = {}
    ingested = 0
    for row in candidates:
        (
            call_id,
            agent_id,
            client_id,
            call_sid,
            known_conversation,
            stale,
            assistant_id,
            insight_map_raw,
            fields_raw,
        ) = row
        agent_key = str(agent_id)
        if agent_key not in conversations_by_agent:
            try:
                conversations = await gateway.find_conversations(assistant_id=assistant_id)
            except Exception as exc:
                # Offline (hotspot mid-call, observed live): no verdicts
                # of any kind on stale information — next tick retries.
                print(f"results ingest: conversations query failed: {exc!r}", flush=True)
                continue
            conversations_by_agent[agent_key] = {
                str(c.get("metadata", {}).get("call_control_id", "")): str(c["id"])
                for c in conversations
                if isinstance(c.get("metadata"), dict)
            }
        conversation_id = str(known_conversation) if known_conversation else None
        if conversation_id is None:
            conversation_id = conversations_by_agent[agent_key].get(str(call_sid or ""))

        if conversation_id is None:
            if bool(stale):
                # The absence is real (the query succeeded): stop
                # polling by writing an empty mirror.
                async with db.worker_session() as s:
                    await _store(s, call_id, client_id, agent_id, [], [])
            continue

        insight_data = await gateway.get_conversation_insights(conversation_id=conversation_id)
        results: dict[str, Any] = {}
        completed = False
        insight_map = _jsonish(insight_map_raw)
        field_by_insight = {v: k for k, v in insight_map.items()}
        for entry in insight_data:
            if entry.get("status") not in (None, "completed"):
                continue
            for item in entry.get("conversation_insights", []):
                field_id = field_by_insight.get(item.get("insight_id"))
                if field_id is not None:
                    results[field_id] = _parse_result(item.get("result", ""))
            completed = True
        if not completed:
            continue  # scored ~2s after hangup; a later tick collects it

        messages = await gateway.get_conversation_messages(conversation_id=conversation_id)
        messages = sorted(messages, key=lambda m: str(m.get("created_at", "")))

        key_by_id = {str(f["id"]): str(f["key"]) for f in _jsonish(fields_raw)}
        rows = []
        for field_id, value in results.items():
            quote, at = provenance(messages, str(value))
            rows.append(
                {
                    "field_id": int(field_id),
                    "field_key": key_by_id.get(field_id, field_id),
                    "value": str(value),
                    "quote": quote,
                    "at": at,
                }
            )
        async with db.worker_session() as s:
            await _store(s, call_id, client_id, agent_id, messages, rows)
            await s.execute(
                text("UPDATE call SET telnyx_conversation_id = :conv WHERE id = :id"),
                {"conv": conversation_id, "id": str(call_id)},
            )
            await notify_run(s, agent_id)
        ingested += 1
    return ingested


# ------------------------------------------------------------- the table


def parse_filters(params: dict[str, str], outputs: list[Any]) -> dict[int, str]:
    """f_<field_id>=<value> query params -> {field_id: value}, keeping
    only fields that exist in the frozen schema."""
    valid = {f.id for f in outputs}
    filters: dict[int, str] = {}
    for key, value in params.items():
        if key.startswith("f_") and key[2:].isdigit() and value.strip():
            field_id = int(key[2:])
            if field_id in valid:
                filters[field_id] = value.strip()
    return filters


async def filtered_calls(
    session: AsyncSession,
    *,
    agent_id: uuid.UUID,
    filters: dict[int, str],
    enum_field_ids: set[int],
) -> list[dict[str, Any]]:
    """Completed calls matching every filter (FR-RESULT-2/3), newest
    first, with their results pivoted onto the row. Filters read the
    corrected value when one exists — a correction changes what the
    data says, so it changes what the filter finds."""
    where = ["c.agent_id = :aid", "c.status = 'completed'"]
    params: dict[str, Any] = {"aid": str(agent_id)}
    for i, (field_id, value) in enumerate(filters.items()):
        # Only parameter NAMES are interpolated (fv0, ff1, ...) plus a
        # literal chosen from two constants; every user value is bound.
        op = "=" if field_id in enum_field_ids else "ILIKE"
        val_param = f"fv{i}"
        params[f"ff{i}"] = field_id
        params[val_param] = value if field_id in enum_field_ids else f"%{value}%"
        where.append(
            f"EXISTS (SELECT 1 FROM insight_result r WHERE r.call_id = c.id"  # noqa: S608
            f" AND r.field_id = :ff{i}"
            f" AND coalesce(r.corrected_value, r.value) {op} :{val_param})"
        )
    rows = (
        await session.execute(
            text(
                # Composed from the fixed clauses above; values are bound.
                "SELECT c.id, c.started_at, c.duration_sec, c.answered_by,"  # noqa: S608
                " c.telnyx_recording_id, ct.phone_e164, ct.variables"
                " FROM call c LEFT JOIN contact ct ON ct.id = c.contact_id"
                f" WHERE {' AND '.join(where)} ORDER BY c.started_at DESC NULLS LAST, c.id"
            ),
            params,
        )
    ).all()
    calls = [
        {
            "id": r[0],
            "started_at": r[1],
            "duration_sec": r[2],
            "answered_by": r[3],
            "recording_id": r[4],
            "phone": r[5],
            "contact_variables": {str(k): str(v) for k, v in _jsonish(r[6] or {}).items()},
            "results": {},
        }
        for r in rows
    ]
    if calls:
        by_id = {str(c["id"]): c for c in calls}
        result_rows = (
            await session.execute(
                text(
                    "SELECT call_id, field_id, field_key, value, corrected_value,"
                    " source_quote, source_timestamp_sec, corrected_at"
                    " FROM insight_result WHERE agent_id = :aid"
                ),
                {"aid": str(agent_id)},
            )
        ).all()
        for call_id, field_id, key, value, corrected, quote, at, corrected_at in result_rows:
            call = by_id.get(str(call_id))
            if call is not None:
                call["results"][int(field_id)] = {
                    "key": key,
                    "value": value,
                    "corrected_value": corrected,
                    "shown": corrected if corrected is not None else value,
                    "quote": quote,
                    "at": float(at) if at is not None else None,
                    "corrected_at": corrected_at,
                }
    return calls


async def run_counters(session: AsyncSession, agent_id: uuid.UUID) -> dict[str, Any]:
    """One snapshot for the live monitor: queue states, agent status,
    and how many calls already have results."""
    states = (
        await session.execute(
            text("SELECT state, count(*) FROM queue_item WHERE agent_id = :aid GROUP BY state"),
            {"aid": str(agent_id)},
        )
    ).all()
    status = (
        await session.execute(
            text("SELECT status FROM agent WHERE id = :aid"), {"aid": str(agent_id)}
        )
    ).scalar_one_or_none()
    with_results = (
        await session.execute(
            text("SELECT count(DISTINCT call_id) FROM insight_result WHERE agent_id = :aid"),
            {"aid": str(agent_id)},
        )
    ).scalar_one()
    counters = {state: int(n) for state, n in states}
    return {
        "status": status,
        "pending": counters.get("pending", 0),
        "dialling": counters.get("dialling", 0),
        "done": counters.get("done", 0),
        "failed": counters.get("failed", 0),
        "skipped": counters.get("skipped", 0),
        "with_results": int(with_results),
    }


async def saved_views(session: AsyncSession, agent_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            text("SELECT id, name, filters FROM saved_view WHERE agent_id = :aid ORDER BY name"),
            {"aid": str(agent_id)},
        )
    ).all()
    return [{"id": r[0], "name": r[1], "filters": _jsonish(r[2])} for r in rows]


async def save_view(
    session: AsyncSession,
    *,
    agent_id: uuid.UUID,
    client_account_id: uuid.UUID,
    name: str,
    filters: dict[int, str],
) -> None:
    """FR-RESULT-3: any combination of filters is a valid view. Saving
    an existing name replaces its filters."""
    await session.execute(
        text(
            """
            INSERT INTO saved_view (agent_id, client_account_id, name, filters)
            VALUES (:aid, :cid, :name, :filters)
            ON CONFLICT (agent_id, name) DO UPDATE SET filters = excluded.filters
            """
        ),
        {
            "aid": str(agent_id),
            "cid": str(client_account_id),
            "name": name.strip(),
            "filters": json.dumps({str(k): v for k, v in filters.items()}),
        },
    )


async def correct_value(
    session: AsyncSession,
    *,
    call_id: uuid.UUID,
    field_id: int,
    corrected_value: str,
    corrected_by: uuid.UUID,
) -> None:
    """FR-RESULT-7: stored ALONGSIDE the original, never over it. Our
    copy and Telnyx's diverge from this moment — both retrievable."""
    await session.execute(
        text(
            "UPDATE insight_result SET corrected_value = :val, corrected_by = :by,"
            " corrected_at = now() WHERE call_id = :call AND field_id = :fid"
        ),
        {
            "val": corrected_value.strip() or None,
            "by": str(corrected_by),
            "call": str(call_id),
            "fid": field_id,
        },
    )


async def transcript_messages(
    session: AsyncSession, call_id: uuid.UUID
) -> list[dict[str, Any]] | None:
    row = (
        await session.execute(
            text("SELECT messages FROM transcript WHERE call_id = :id"), {"id": str(call_id)}
        )
    ).first()
    if row is None:
        return None
    messages = _jsonish(row[0])
    return messages if isinstance(messages, list) else []


async def _store(
    session: AsyncSession,
    call_id: uuid.UUID,
    client_id: uuid.UUID,
    agent_id: uuid.UUID,
    messages: list[dict[str, Any]],
    rows: list[dict[str, Any]],
) -> None:
    await session.execute(
        text(
            "INSERT INTO transcript (call_id, client_account_id, agent_id, messages)"
            " VALUES (:call, :cid, :aid, :messages) ON CONFLICT (call_id) DO NOTHING"
        ),
        {
            "call": str(call_id),
            "cid": str(client_id),
            "aid": str(agent_id),
            "messages": json.dumps(messages),
        },
    )
    for r in rows:
        await session.execute(
            text(
                """
                INSERT INTO insight_result (call_id, client_account_id, agent_id,
                                            field_id, field_key, value,
                                            source_quote, source_timestamp_sec)
                VALUES (:call, :cid, :aid, :fid, :key, :value, :quote, :at)
                ON CONFLICT (call_id, field_id) DO NOTHING
                """
            ),
            {
                "call": str(call_id),
                "cid": str(client_id),
                "aid": str(agent_id),
                "fid": r["field_id"],
                "key": r["field_key"],
                "value": r["value"],
                "quote": r["quote"],
                "at": r["at"],
            },
        )
