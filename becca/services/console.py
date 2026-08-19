"""Console operations (FR-CONSOLE-3..7, FR-NOTIFY-2A).

Two provenance rules run through this module:

- Telnyx account state (balance, number inventory, destinations) is read
  live when a console screen renders — never cached by a background job
  (techstack: "read it when the landing screen renders"). A Telnyx
  failure degrades that panel to "unavailable"; it never breaks the page,
  because the console is also where staff go when Telnyx is the problem.
- What the API cannot tell us (verification tier, regulatory document
  expiry) is staff-recorded in telnyx_account_note and labelled as such
  in the UI (FR-NOTIFY-2B: the v1 backstop is human).
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from becca.telnyx.gateway import TelnyxError, TelnyxGateway

# At most one balance snapshot per hour: enough resolution for a
# projected-exhaustion date, no unbounded growth.
SNAPSHOT_MIN_INTERVAL_MINUTES = 60
# Burn rate is measured over this window; older snapshots reflect
# spending patterns that no longer predict anything.
BURN_WINDOW_DAYS = 14


class AllocationExceeded(Exception):
    """FR-CONSOLE-3: allocations are zero-sum against the ceiling."""


class ReassignmentRefused(Exception):
    """FR-CONSOLE-5: a number does not move while a run is dialling on it."""


# ---------------------------------------------------------------- balance


@dataclass(frozen=True)
class BalanceOverview:
    available: bool
    credit: float = 0.0
    currency: str = "USD"
    burn_per_day: float | None = None
    exhaustion_date: date | None = None
    days_left: float | None = None
    # True when this render inserted the hourly snapshot — the landing
    # screen piggybacks the cost sync on the same cadence (FR-BILL-2's
    # "on console page load", throttled to once an hour).
    snapshotted: bool = False


async def balance_overview(session: AsyncSession, gateway: TelnyxGateway) -> BalanceOverview:
    """Live balance + projected exhaustion (FR-CONSOLE-6/7, FR-NOTIFY-2A).

    Projection is linear over observed snapshots, which is honest for a
    single-client agency: no seasonality model, just "at the burn we have
    actually watched, it runs out on this date"."""
    try:
        data = await gateway.get_balance()
    except Exception:
        return BalanceOverview(available=False)
    credit = float(data.get("available_credit") or data.get("balance") or 0)
    currency = str(data.get("currency") or "USD")

    last = (
        await session.execute(
            text("SELECT taken_at FROM balance_snapshot ORDER BY taken_at DESC LIMIT 1")
        )
    ).scalar_one_or_none()
    snapshotted = last is None or datetime.now(UTC) - last > timedelta(
        minutes=SNAPSHOT_MIN_INTERVAL_MINUTES
    )
    if snapshotted:
        await session.execute(
            text("INSERT INTO balance_snapshot (available_credit, currency) VALUES (:c, :cur)"),
            {"c": credit, "cur": currency},
        )

    row = (
        await session.execute(
            text(
                """
                SELECT min(taken_at), max(taken_at),
                       (array_agg(available_credit ORDER BY taken_at ASC))[1],
                       (array_agg(available_credit ORDER BY taken_at DESC))[1]
                  FROM balance_snapshot
                 WHERE taken_at > now() - make_interval(days => :days)
                """
            ),
            {"days": BURN_WINDOW_DAYS},
        )
    ).one()
    first_at, last_at, first_credit, last_credit = row
    burn = exhaustion = days_left = None
    if first_at is not None and last_at is not None and first_at != last_at:
        span_days = _span_days(first_at, last_at)
        spent = float(first_credit) - float(last_credit)
        if span_days > 0 and spent > 0:
            burn = spent / span_days
            days_left = credit / burn
            exhaustion = (datetime.now(UTC) + timedelta(days=days_left)).date()
    return BalanceOverview(
        available=True,
        credit=credit,
        currency=currency,
        burn_per_day=burn,
        exhaustion_date=exhaustion,
        days_left=days_left,
        snapshotted=snapshotted,
    )


def _span_days(first_at: datetime, last_at: datetime) -> float:
    return (last_at - first_at).total_seconds() / 86400


# ------------------------------------------------------------- allocation


async def allocation_split(session: AsyncSession, *, ceiling: int) -> dict[str, Any]:
    """The zero-sum picture (FR-CONSOLE-3): per-client slices, in-use
    counts, and the unallocated remainder."""
    rows = (
        await session.execute(
            text(
                """
                SELECT c.id, c.name, c.channel_allocation,
                       (SELECT count(*) FROM call k
                         WHERE k.client_account_id = c.id AND k.status = 'dialling')
                  FROM client_account c ORDER BY c.channel_allocation DESC, c.created_at
                """
            )
        )
    ).all()
    clients = [
        {"id": r[0], "name": r[1], "allocation": int(r[2]), "in_use": int(r[3])} for r in rows
    ]
    allocated = sum(c["allocation"] for c in clients)
    return {
        "clients": clients,
        "allocated": allocated,
        "ceiling": ceiling,
        "unallocated": max(ceiling - allocated, 0),
    }


async def set_allocation(
    session: AsyncSession, *, client_account_id: uuid.UUID, channels: int, ceiling: int
) -> None:
    """Zero-sum enforced in one statement: the UPDATE only lands if every
    OTHER client's allocation plus the new value fits the ceiling, so two
    concurrent edits cannot jointly oversubscribe (FR-CONSOLE-3,
    FR-DISPATCH-9's 'sum of allocations never exceeds the ceiling')."""
    if channels < 0:
        raise AllocationExceeded("allocation cannot be negative")
    updated = (
        await session.execute(
            text(
                """
                UPDATE client_account SET channel_allocation = :n
                 WHERE id = :cid
                   AND :n + (SELECT coalesce(sum(channel_allocation), 0)
                               FROM client_account WHERE id != :cid) <= :ceiling
                RETURNING id
                """
            ),
            {"n": channels, "cid": str(client_account_id), "ceiling": ceiling},
        )
    ).scalar_one_or_none()
    if updated is None:
        raise AllocationExceeded(
            f"{channels} channels would exceed the account ceiling of {ceiling}"
        )
    await refresh_client_status(session, client_account_id=client_account_id)


# ---------------------------------------------------------------- numbers


async def sync_numbers(session: AsyncSession, gateway: TelnyxGateway) -> bool:
    """Upsert inventory facts from Telnyx (FR-CONSOLE-5). Telnyx owns
    what numbers exist and their status; the client assignment column is
    ours and is never touched here. Returns False when Telnyx was
    unreachable (the screen then shows the last-known inventory)."""
    try:
        records = await gateway.list_phone_numbers()
    except Exception:
        return False
    for r in records:
        await session.execute(
            text(
                """
                INSERT INTO phone_number (telnyx_phone_number_id, phone_e164, country, status)
                VALUES (:tid, :e164, :country, :status)
                ON CONFLICT (phone_e164) DO UPDATE
                    SET telnyx_phone_number_id = excluded.telnyx_phone_number_id,
                        status = excluded.status,
                        country = excluded.country
                """
            ),
            {
                "tid": str(r.get("id") or "") or None,
                "e164": str(r.get("phone_number")),
                "country": str(r.get("country_iso_alpha2") or "NG"),
                "status": str(r.get("status") or "active"),
            },
        )
    return True


async def list_inventory(session: AsyncSession) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            text(
                """
                SELECT n.id, n.phone_e164, n.country, n.number_use, n.status,
                       n.client_account_id, c.name,
                       EXISTS (SELECT 1 FROM agent a
                                WHERE a.client_account_id = n.client_account_id
                                  AND a.status = 'dialling') AS client_dialling
                  FROM phone_number n
                  LEFT JOIN client_account c ON c.id = n.client_account_id
                 ORDER BY n.created_at
                """
            )
        )
    ).all()
    return [
        {
            "id": r[0],
            "e164": r[1],
            "country": r[2],
            "use": r[3],
            "status": r[4],
            "client_id": r[5],
            "client_name": r[6],
            "dialling": bool(r[7]) and r[5] is not None,
        }
        for r in rows
    ]


async def assign_number(
    session: AsyncSession,
    *,
    phone_number_id: uuid.UUID,
    client_account_id: uuid.UUID | None,
) -> None:
    """Assign or reassign (client_account_id=None unassigns). Refused
    while the currently assigned client has a run dialling — the number
    is that run's caller ID (FR-CONSOLE-5)."""
    current = (
        await session.execute(
            text("SELECT client_account_id FROM phone_number WHERE id = :nid FOR UPDATE"),
            {"nid": str(phone_number_id)},
        )
    ).scalar_one_or_none()
    if current is not None:
        dialling = (
            await session.execute(
                text(
                    "SELECT count(*) FROM agent"
                    " WHERE client_account_id = :cid AND status = 'dialling'"
                ),
                {"cid": str(current)},
            )
        ).scalar_one()
        if int(dialling) > 0:
            raise ReassignmentRefused("a run is dialling on this number; pause or finish it first")
    await session.execute(
        text(
            "UPDATE phone_number SET client_account_id = cast(:cid AS uuid),"
            " assigned_at = CASE WHEN cast(:cid AS uuid) IS NULL THEN NULL ELSE now() END"
            " WHERE id = :nid"
        ),
        {"cid": str(client_account_id) if client_account_id else None, "nid": str(phone_number_id)},
    )
    if client_account_id is not None:
        await refresh_client_status(session, client_account_id=client_account_id)


async def order_number(session: AsyncSession, gateway: TelnyxGateway) -> str:
    """Search + order the first available Nigerian number, then sync so
    it appears in inventory immediately (FR-CONSOLE-5)."""
    found = await gateway.search_available_numbers(country_code="NG", limit=1)
    if not found:
        raise TelnyxError(404, "no Nigerian numbers available to order right now")
    e164 = str(found[0]["phone_number"])
    await gateway.order_number(phone_number=e164)
    await sync_numbers(session, gateway)
    return e164


# ------------------------------------------------------ clients & health


async def client_rows(session: AsyncSession) -> list[dict[str, Any]]:
    """FR-CONSOLE-1: people, channels, number, calls MTD, billed MTD,
    status. 'Billed' is what the wallet ledger debited this month (calls
    and test calls) — the same figure the margin monitor bills against."""
    rows = (
        await session.execute(
            text(
                """
                SELECT c.id, c.name, c.status, c.channel_allocation, c.wallet_balance_usd,
                       (SELECT count(*) FROM app_user u WHERE u.client_account_id = c.id),
                       (SELECT n.phone_e164 FROM phone_number n
                         WHERE n.client_account_id = c.id AND n.number_use = 'outbound'
                         ORDER BY n.assigned_at DESC LIMIT 1),
                       (SELECT count(*) FROM call k
                         WHERE k.client_account_id = c.id
                           AND k.started_at >= date_trunc('month', now())),
                       coalesce((SELECT -sum(wl.amount_usd) FROM wallet_ledger wl
                         WHERE wl.client_account_id = c.id
                           AND wl.entry_type IN ('debit_call', 'debit_test_call')
                           AND wl.created_at >= date_trunc('month', now())), 0),
                       EXISTS (SELECT 1 FROM agent a
                                WHERE a.client_account_id = c.id AND a.status = 'dialling'),
                       c.acknowledged_at
                  FROM client_account c ORDER BY c.created_at
                """
            )
        )
    ).all()
    return [
        {
            "id": r[0],
            "name": r[1],
            "status": "dialling" if r[9] else r[2],
            "channels": int(r[3]),
            "balance": float(r[4]),
            "people": int(r[5]),
            "number": r[6],
            "calls_mtd": int(r[7]),
            "billed_mtd": float(r[8]),
            "acknowledged": r[10] is not None,
        }
        for r in rows
    ]


async def onboarding_checklist(
    session: AsyncSession, *, client_account_id: uuid.UUID
) -> list[dict[str, Any]]:
    """FR-CONSOLE-4, mirroring the launch pre-flight's account-level
    blocking conditions so the console never promises what launch
    refuses."""
    row = (
        await session.execute(
            text(
                """
                SELECT c.name, c.rate_per_min_usd, c.acknowledged_at, c.channel_allocation,
                       (SELECT count(*) FROM app_user u
                         WHERE u.client_account_id = c.id AND u.role = 'owner'),
                       (SELECT count(*) FROM phone_number n WHERE n.client_account_id = c.id),
                       (SELECT count(*) FROM agent a
                         WHERE a.client_account_id = c.id AND a.status != 'draft'),
                       c.wallet_balance_usd
                  FROM client_account c WHERE c.id = :cid
                """
            ),
            {"cid": str(client_account_id)},
        )
    ).one()
    return [
        {"label": "Client account created", "ok": True},
        {"label": f"Rate set: ${float(row[1]):.2f} per minute", "ok": float(row[1]) > 0},
        {"label": "Wallet has an opening credit", "ok": float(row[7]) > 0},
        {"label": "Owner email added", "ok": int(row[4]) > 0},
        {
            "label": "Owner has accepted the contact acknowledgement",
            "ok": row[2] is not None,
        },
        {"label": "Channels allocated", "ok": int(row[3]) > 0},
        {"label": "Number assigned", "ok": int(row[5]) > 0},
        {"label": "First agent past draft", "ok": int(row[6]) > 0},
    ]


async def refresh_client_status(session: AsyncSession, *, client_account_id: uuid.UUID) -> None:
    """onboarding → active once the launch-blocking trio (acknowledgement,
    channels, number) is in place. One direction only: an active account
    never demotes itself."""
    await session.execute(
        text(
            """
            UPDATE client_account SET status = 'active'
             WHERE id = :cid AND status = 'onboarding'
               AND acknowledged_at IS NOT NULL AND channel_allocation > 0
               AND EXISTS (SELECT 1 FROM phone_number n WHERE n.client_account_id = :cid)
            """
        ),
        {"cid": str(client_account_id)},
    )


async def destinations(gateway: TelnyxGateway) -> list[str] | None:
    """ISO country codes enabled on the outbound voice profile(s), or
    None when Telnyx was unreachable (FR-CONSOLE-6)."""
    try:
        profiles = await gateway.list_outbound_voice_profiles()
    except Exception:
        return None
    enabled: list[str] = []
    for p in profiles:
        for c in p.get("whitelisted_destinations") or []:
            if c not in enabled:
                enabled.append(str(c))
    return enabled


async def account_note(session: AsyncSession) -> dict[str, Any] | None:
    row = (
        await session.execute(
            text(
                "SELECT n.verification_tier, n.doc_expiry, n.updated_at, s.google_email"
                " FROM telnyx_account_note n"
                " LEFT JOIN becca_staff s ON s.id = n.noted_by_staff_id WHERE n.id"
            )
        )
    ).first()
    if row is None:
        return None
    return {
        "verification_tier": row[0],
        "doc_expiry": row[1],
        "updated_at": row[2],
        "updated_by": row[3],
    }


async def save_account_note(
    session: AsyncSession,
    *,
    verification_tier: str,
    doc_expiry: date | None,
    staff_id: uuid.UUID,
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO telnyx_account_note
                (id, verification_tier, doc_expiry, noted_by_staff_id, updated_at)
            VALUES (true, :tier, :expiry, :sid, now())
            ON CONFLICT (id) DO UPDATE
                SET verification_tier = excluded.verification_tier,
                    doc_expiry = excluded.doc_expiry,
                    noted_by_staff_id = excluded.noted_by_staff_id,
                    updated_at = now()
            """
        ),
        {"tier": verification_tier.strip() or None, "expiry": doc_expiry, "sid": str(staff_id)},
    )
