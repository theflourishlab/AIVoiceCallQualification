"""The client Overview (prototype screen 01, built 14 Aug 2026): the
first client-wide aggregates in the codebase — every earlier helper is
agent-scoped.

Content decisions (grilled 14 Aug 2026, recorded in the plan):
- "Reached" ≡ status='completed' AND duration_sec > 0 — reached ⇔
  billed, so this number and the wallet can never disagree.
- "Results captured" counts DISTINCT calls with any insight_result row:
  a volume metric, deliberately agent-agnostic. "Qualified" is absent
  by decision — it is a per-agent saved view the system holds no
  opinion about (CONTEXT.md).
- Outcomes are three honest slices: reached / didn't connect (failures
  plus zero-second completions) / still dialling. No voicemail slice —
  AMD has never been observed live.
- "Today" starts at midnight Africa/Lagos (SD-21: one timezone per
  account, hardcoded exactly as launch.py does). This is the repo's
  first timezone-aware SQL; Lagos is UTC+1 with no DST, so the
  conversion is fixed. The UTC precedent would roll the day at 1 a.m.
  Lagos wall-clock — a client-facing bug.

Run calls only in call-derived figures; spend is the whole ledger
(test calls included — money is money).
"""

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Interpolated into SQL below as a trusted literal (never user input).
_TODAY = "date_trunc('day', now() AT TIME ZONE 'Africa/Lagos') AT TIME ZONE 'Africa/Lagos'"


async def live_state(session: AsyncSession, *, client_account_id: uuid.UUID) -> dict[str, Any]:
    """The note-bar: is anything running, and how full are the lines."""
    row = (
        await session.execute(
            text(
                """
                SELECT (SELECT count(*) FROM agent a
                          JOIN run_schedule rs ON rs.agent_id = a.id
                         WHERE a.client_account_id = :cid
                           AND a.status = 'dialling' AND NOT rs.paused),
                       (SELECT count(*) FROM call
                         WHERE client_account_id = :cid AND status = 'dialling'),
                       (SELECT channel_allocation FROM client_account WHERE id = :cid)
                """
            ),
            {"cid": str(client_account_id)},
        )
    ).one()
    return {
        "dialling_agents": int(row[0]),
        "in_flight": int(row[1]),
        "allocation": int(row[2] or 0),
    }


async def today_kpis(session: AsyncSession, *, client_account_id: uuid.UUID) -> dict[str, Any]:
    """The four tiles. started_at is stamped at dial for every placed
    call; pre-send failures carry only ended_at, hence the coalesce."""
    row = (
        await session.execute(
            text(
                f"""
                SELECT
                  (SELECT count(*) FROM call
                    WHERE client_account_id = :cid
                      AND coalesce(started_at, ended_at) >= {_TODAY}),
                  (SELECT count(*) FROM call
                    WHERE client_account_id = :cid AND status = 'completed'
                      AND duration_sec > 0 AND started_at >= {_TODAY}),
                  (SELECT count(DISTINCT r.call_id)
                     FROM insight_result r JOIN call k ON k.id = r.call_id
                    WHERE r.client_account_id = :cid AND k.started_at >= {_TODAY}),
                  (SELECT coalesce(-sum(wl.amount_usd), 0) FROM wallet_ledger wl
                    WHERE wl.client_account_id = :cid
                      AND wl.entry_type IN ('debit_call', 'debit_test_call')
                      AND wl.created_at >= {_TODAY}),
                  (SELECT coalesce(sum(wl.billed_minutes), 0) FROM wallet_ledger wl
                    WHERE wl.client_account_id = :cid
                      AND wl.entry_type IN ('debit_call', 'debit_test_call')
                      AND wl.created_at >= {_TODAY})
                """  # noqa: S608 — _TODAY is a module literal, never user input
            ),
            {"cid": str(client_account_id)},
        )
    ).one()
    return {
        "calls": int(row[0]),
        "reached": int(row[1]),
        "results": int(row[2]),
        "spend": float(row[3]),
        "billed_minutes": int(row[4]),
    }


async def calls_by_hour(session: AsyncSession, *, client_account_id: uuid.UUID) -> list[int]:
    """24 buckets, Lagos wall-clock hours. Honest full day — no
    trimming to business hours; test days and odd windows show up."""
    rows = (
        await session.execute(
            text(
                f"""
                SELECT extract(hour FROM started_at AT TIME ZONE 'Africa/Lagos')::int,
                       count(*)
                  FROM call
                 WHERE client_account_id = :cid
                   AND started_at IS NOT NULL AND started_at >= {_TODAY}
                 GROUP BY 1
                """  # noqa: S608 — _TODAY is a module literal, never user input
            ),
            {"cid": str(client_account_id)},
        )
    ).all()
    hours = [0] * 24
    for hour, n in rows:
        hours[int(hour)] = int(n)
    return hours


async def outcomes(session: AsyncSession, *, client_account_id: uuid.UUID) -> dict[str, int]:
    """Three honest slices over today's calls. A zero-second completion
    counts as 'didn't connect' — nobody was billed, nobody spoke."""
    row = (
        await session.execute(
            text(
                f"""
                SELECT
                  count(*) FILTER (WHERE status = 'completed' AND duration_sec > 0),
                  count(*) FILTER (WHERE status = 'failed'
                     OR (status = 'completed' AND coalesce(duration_sec, 0) = 0)),
                  count(*) FILTER (WHERE status = 'dialling')
                  FROM call
                 WHERE client_account_id = :cid
                   AND coalesce(started_at, ended_at) >= {_TODAY}
                """  # noqa: S608 — _TODAY is a module literal, never user input
            ),
            {"cid": str(client_account_id)},
        )
    ).one()
    return {"reached": int(row[0]), "failed": int(row[1]), "dialling": int(row[2])}


async def agents_table(
    session: AsyncSession, *, client_account_id: uuid.UUID
) -> list[dict[str, Any]]:
    """Launched agents (a run_schedule row exists), newest first —
    lifetime run figures, matching the /results index's scope. Draft
    agents belong to the Agents screen, not here."""
    rows = (
        await session.execute(
            text(
                """
                SELECT a.id, a.name, a.status, rs.paused, cl.filename,
                       (SELECT count(*) FROM queue_item qi
                         WHERE qi.agent_id = a.id AND qi.state = 'done'),
                       (SELECT count(*) FROM queue_item qi WHERE qi.agent_id = a.id),
                       (SELECT count(*) FROM call k
                         WHERE k.agent_id = a.id AND k.status = 'completed'
                           AND k.duration_sec > 0),
                       (SELECT count(DISTINCT r.call_id) FROM insight_result r
                         WHERE r.agent_id = a.id)
                  FROM agent a
                  JOIN run_schedule rs ON rs.agent_id = a.id
                  LEFT JOIN contact_list cl ON cl.id = rs.contact_list_id
                 WHERE a.client_account_id = :cid
                 ORDER BY rs.created_at DESC
                """
            ),
            {"cid": str(client_account_id)},
        )
    ).all()
    return [
        {
            "id": str(r[0]),
            "name": r[1],
            "status": r[2],
            "paused": bool(r[3]),
            "list": r[4],
            "done": int(r[5]),
            "total": int(r[6]),
            "reached": int(r[7]),
            "results": int(r[8]),
        }
        for r in rows
    ]
