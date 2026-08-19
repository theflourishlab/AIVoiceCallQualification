"""In-app notifications (FR-NOTIFY-1/2B/3).

The honest contract: a user discovers an event when they next open the
app. That is acceptable because every client-facing failure is
fail-safe (FR-NOTIFY-2) — nothing continues going wrong while unread.
The one exception, low balance, is ALSO a persistent console banner
(FR-NOTIFY-2A); its notification row here is the record, not the alert.

Emitters call emit() with the audience: a client_account_id for client
events, None for Becca-side. Preferences filter at READ time, so
toggling an event off hides existing rows too — the table stores only
overrides (absent row = enabled).
"""

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# The FR-NOTIFY-2B table, verbatim. Port-completed / verification-
# lapsing events are deliberately absent: nothing in the system observes
# ports, and doc expiry is staff-recorded on the numbers screen where it
# is already visible (FR-NOTIFY-2B: the v1 backstop is human).
CLIENT_EVENTS = {
    "run_halted": "Agent stopped dialling",
    "spend_cap_reached": "Spend cap reached",
    "import_blocked": "Import had rows that cannot be dialled",
    "run_finished": "Agent finished — results ready",
    # Wallet (14 Aug 2026). Silent repricing is the one thing a prepaid
    # model must never do, hence rate_changed.
    "wallet_low": "Wallet balance low",
    "run_paused_no_balance": "Agent paused — wallet balance too low",
    "wallet_credited": "Wallet topped up",
    "rate_changed": "Your per-minute call rate changed",
}
STAFF_EVENTS = {
    "pool_exhausted": "Channel pool exhausted",
    "balance_low": "Telnyx balance low",
    "client_wallet_low": "A client's wallet is low",
}


async def emit(
    session: AsyncSession,
    *,
    event: str,
    title: str,
    body: str = "",
    client_account_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None,
    dedupe_hours: int | None = None,
) -> bool:
    """Record one event. With dedupe_hours, a same-event row for the
    same audience within the window suppresses this one — recurring
    conditions (low balance on every render) become one row per day,
    not a flood. Returns whether a row was written."""
    if dedupe_hours is not None:
        recent = (
            await session.execute(
                text(
                    "SELECT count(*) FROM notification"
                    " WHERE event = :e"
                    " AND client_account_id IS NOT DISTINCT FROM cast(:cid AS uuid)"
                    " AND created_at > now() - make_interval(hours => :h)"
                ),
                {
                    "e": event,
                    "cid": str(client_account_id) if client_account_id else None,
                    "h": dedupe_hours,
                },
            )
        ).scalar_one()
        if int(recent) > 0:
            return False
    await session.execute(
        text(
            "INSERT INTO notification (client_account_id, event, title, body, agent_id)"
            " VALUES (cast(:cid AS uuid), :e, :t, :b, cast(:aid AS uuid))"
        ),
        {
            "cid": str(client_account_id) if client_account_id else None,
            "e": event,
            "t": title,
            "b": body,
            "aid": str(agent_id) if agent_id else None,
        },
    )
    return True


def _audience_clause(client_account_id: uuid.UUID | None) -> dict[str, Any]:
    return {"cid": str(client_account_id) if client_account_id else None}


async def unread_count(
    session: AsyncSession,
    *,
    reader_id: uuid.UUID,
    client_account_id: uuid.UUID | None,
) -> int:
    """Unread AND enabled — a preference toggled off silences the badge
    for existing rows too."""
    n = (
        await session.execute(
            text(
                """
                SELECT count(*) FROM notification n
                 WHERE n.client_account_id IS NOT DISTINCT FROM cast(:cid AS uuid)
                   AND NOT EXISTS (SELECT 1 FROM notification_read r
                                    WHERE r.notification_id = n.id AND r.reader_id = :rid)
                   AND NOT EXISTS (SELECT 1 FROM notification_pref p
                                    WHERE p.reader_id = :rid AND p.event = n.event
                                      AND NOT p.enabled)
                """
            ),
            {"rid": str(reader_id), **_audience_clause(client_account_id)},
        )
    ).scalar_one()
    return int(n)


async def list_for(
    session: AsyncSession,
    *,
    reader_id: uuid.UUID,
    client_account_id: uuid.UUID | None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            text(
                """
                SELECT n.id, n.event, n.title, n.body, n.agent_id, n.created_at,
                       EXISTS (SELECT 1 FROM notification_read r
                                WHERE r.notification_id = n.id AND r.reader_id = :rid)
                  FROM notification n
                 WHERE n.client_account_id IS NOT DISTINCT FROM cast(:cid AS uuid)
                   AND NOT EXISTS (SELECT 1 FROM notification_pref p
                                    WHERE p.reader_id = :rid AND p.event = n.event
                                      AND NOT p.enabled)
                 ORDER BY n.created_at DESC LIMIT :lim
                """
            ),
            {"rid": str(reader_id), "lim": limit, **_audience_clause(client_account_id)},
        )
    ).all()
    return [
        {
            "id": r[0],
            "event": r[1],
            "title": r[2],
            "body": r[3],
            "agent_id": r[4],
            "created_at": r[5],
            "read": bool(r[6]),
        }
        for r in rows
    ]


async def mark_all_read(
    session: AsyncSession,
    *,
    reader_id: uuid.UUID,
    client_account_id: uuid.UUID | None,
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO notification_read (notification_id, client_account_id, reader_id)
            SELECT n.id, n.client_account_id, :rid FROM notification n
             WHERE n.client_account_id IS NOT DISTINCT FROM cast(:cid AS uuid)
            ON CONFLICT DO NOTHING
            """
        ),
        {"rid": str(reader_id), **_audience_clause(client_account_id)},
    )


async def prefs_for(
    session: AsyncSession,
    *,
    reader_id: uuid.UUID,
    events: dict[str, str],
) -> list[dict[str, Any]]:
    """Every event for this audience with its effective enabled state."""
    rows = (
        await session.execute(
            text("SELECT event, enabled FROM notification_pref WHERE reader_id = :rid"),
            {"rid": str(reader_id)},
        )
    ).all()
    overrides: dict[str, bool] = {str(r[0]): bool(r[1]) for r in rows}
    return [
        {"event": e, "label": label, "enabled": bool(overrides.get(e, True))}
        for e, label in events.items()
    ]


async def set_pref(
    session: AsyncSession,
    *,
    reader_id: uuid.UUID,
    client_account_id: uuid.UUID | None,
    event: str,
    enabled: bool,
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO notification_pref (reader_id, client_account_id, event, enabled)
            VALUES (:rid, cast(:cid AS uuid), :e, :en)
            ON CONFLICT (reader_id, event) DO UPDATE SET enabled = excluded.enabled
            """
        ),
        {
            "rid": str(reader_id),
            "e": event,
            "en": enabled,
            **_audience_clause(client_account_id),
        },
    )
