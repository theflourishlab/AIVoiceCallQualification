"""Pair each stored transcript with its instruction contract and score it.

A transcript is only scoreable against what its call was actually told
(the rubric's ground truth), so loading means reconstructing that
contract per source:

- test_run rows carry agent_version_id (the exact version dialled) and
  stand_ins (values keyed by field key, straight from the test form).
- call/transcript rows resolve like dispatch did: the agent's current
  version (frozen since launch — AgentFrozen guards non-draft saves)
  plus the contact's variables through resolved_variables, so optional
  fields fall back to their spoken defaults exactly as on the live call.

Loading and scoring are separate passes: all DB reads happen first, so
no transaction stays open across judge network calls.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from becca.db.session import SessionFactory
from becca.domain.codec import content_from_json
from becca.domain.model import AgentVersionContent
from becca.domain.serialize import natural_greeting, render_assistant_instructions
from becca.domain.views import variable_contract
from becca.evals.judge import Judge
from becca.evals.mechanical import run_mechanical_checks
from becca.evals.rubric import CallScorecard
from becca.services.contacts import resolved_variables


def _conversation_model() -> str:
    """The model calls run on now comes from configuration. The DB does
    not record it per call, so score a round while its config is still
    live — the scorecard stamps whatever is configured at scoring time."""
    from becca.config import load_settings

    return load_settings().assistant_conversation_model


@dataclass(frozen=True)
class ScoreableCall:
    label: str
    source: str  # "test_run" | "call"
    agent_name: str
    content: AgentVersionContent
    values: dict[str, str]  # by field key, as the call resolved them
    messages: list[dict[str, Any]]


def _jsonish(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _content(fields: Any, script_blocks: Any) -> AgentVersionContent:
    return content_from_json({"fields": _jsonish(fields), "script_blocks": _jsonish(script_blocks)})


def substituted_instructions(content: AgentVersionContent, values: dict[str, str]) -> str:
    """The judge's copy of the contract: what the assistant received,
    with the provider's substitution already performed, prefixed with
    the fixed greeting so wording there is never blamed on the model."""
    greeting = natural_greeting(content)
    rendered = (
        "FIXED GREETING (spoken verbatim by TTS, not phrased by the agent):\n"
        + greeting
        + "\n\n"
        + render_assistant_instructions(content)
    )
    for key, value in values.items():
        rendered = rendered.replace("{{" + key + "}}", value)
    return rendered


def _scope(
    agent: str | None, since: datetime | None, created_col: str
) -> tuple[str, dict[str, Any]]:
    """WHERE-clause tail + params for the batch scope. A round scores
    only its own calls (one agent, placed after the round began) —
    otherwise every batch re-mixes history and comparisons mean nothing."""
    sql = ""
    params: dict[str, Any] = {}
    if agent:
        sql += " AND a.name ILIKE :agent"
        params["agent"] = f"%{agent}%"
    if since:
        # asyncpg compares timestamptz against real datetimes only;
        # a naive input means UTC.
        sql += f" AND {created_col} >= :since"
        params["since"] = since if since.tzinfo else since.replace(tzinfo=UTC)
    return sql, params


async def load_test_runs(
    session: AsyncSession, *, agent: str | None = None, since: datetime | None = None
) -> list[ScoreableCall]:
    scope_sql, params = _scope(agent, since, "tr.created_at")
    rows = await session.execute(
        text(
            # S608: scope_sql is assembled from our own literals; the
            # agent/since values travel as bind parameters.
            "SELECT tr.n, a.name, v.fields, v.script_blocks, tr.stand_ins, tr.transcript"  # noqa: S608
            " FROM test_run tr"
            " JOIN agent a ON a.id = tr.agent_id"
            " JOIN agent_version v ON v.id = tr.agent_version_id"
            " WHERE tr.status = 'complete' AND tr.transcript IS NOT NULL"
            + scope_sql
            + " ORDER BY tr.created_at"
        ),
        params,
    )
    out = []
    for n, agent_name, fields, blocks, stand_ins, transcript in rows:
        messages = _jsonish(transcript)
        if not messages:
            continue
        out.append(
            ScoreableCall(
                label=f"test-{n}",
                source="test_run",
                agent_name=str(agent_name),
                content=_content(fields, blocks),
                values={str(k): str(v) for k, v in _jsonish(stand_ins).items()},
                messages=messages,
            )
        )
    return out


async def load_run_calls(
    session: AsyncSession, *, agent: str | None = None, since: datetime | None = None
) -> list[ScoreableCall]:
    scope_sql, params = _scope(agent, since, "c.started_at")
    rows = await session.execute(
        text(
            # S608: scope_sql is assembled from our own literals; the
            # agent/since values travel as bind parameters.
            "SELECT c.id, a.name, v.fields, v.script_blocks, ct.variables, t.messages"  # noqa: S608
            " FROM transcript t"
            " JOIN call c ON c.id = t.call_id"
            " JOIN agent a ON a.id = c.agent_id"
            " JOIN agent_version v ON v.id = a.current_version_id"
            " LEFT JOIN contact ct ON ct.id = c.contact_id"
            " WHERE true" + scope_sql + " ORDER BY c.started_at"
        ),
        params,
    )
    out = []
    for call_id, agent_name, fields, blocks, variables, messages_raw in rows:
        messages = _jsonish(messages_raw)
        if not messages:
            continue
        content = _content(fields, blocks)
        raw_vars = {str(k): str(v) for k, v in (_jsonish(variables) or {}).items()}
        resolved = resolved_variables(variable_contract(content), raw_vars)
        by_key = {content.field_by_id(fid).key: value for fid, value in resolved.items()}
        out.append(
            ScoreableCall(
                label=f"call-{str(call_id)[:8]}",
                source="call",
                agent_name=str(agent_name),
                content=content,
                values=by_key,
                messages=messages,
            )
        )
    return out


async def load_scoreable_calls(
    db: SessionFactory, *, agent: str | None = None, since: datetime | None = None
) -> list[ScoreableCall]:
    async with db.console_session() as session:
        return await load_run_calls(session, agent=agent, since=since) + await load_test_runs(
            session, agent=agent, since=since
        )


async def score_call(call: ScoreableCall, judge: Judge) -> CallScorecard:
    field_keys = frozenset(f.key for f in call.content.fields)
    mechanical = run_mechanical_checks(call.messages, field_keys)
    judged, overall = await judge.score(
        instructions=substituted_instructions(call.content, call.values),
        messages=call.messages,
    )
    ordered = tuple(sorted(mechanical + judged, key=lambda v: v.turn))
    return CallScorecard(
        call_label=call.label,
        source=call.source,
        agent_name=call.agent_name,
        conversation_model=_conversation_model(),
        turns=len(call.messages),
        agent_turns=sum(1 for m in call.messages if m.get("role") == "assistant"),
        violations=ordered,
        judge_overall=overall,
    )
