"""The prepaid wallet (14 Aug 2026 direction; supersedes FR-BILL-1/5/7,
inverts FR-BILL-8).

The append-only `wallet_ledger` is the source of truth; the cached
`client_account.wallet_balance_usd` is a convenience updated in the
same transaction as every ledger insert — and ONLY when the insert
actually inserted, which is what keeps settlement idempotent past the
webhook dedupe (redelivered settle: ON CONFLICT swallows the insert,
so the cache never moves twice).

Money flows:
- credit / adjustment: staff, console, audited by the caller.
- debit_call: settled from the TeXML `completed` callback, at the rate
  snapshotted onto the call row when it was claimed. Pro-rata per
  second (24 Aug 2026; supersedes the started-minute round-up):
  rate x sec / 60, half-up to the cent. 0 seconds — or a charge that
  rounds to nothing — bills nothing.
- debit_test_call: same, keyed on test_run.id.

Reservations are COMPUTED, never stored: every in-flight attempt
(call or test_run in 'dialling') holds `rate x max_call_minutes`.
The in-flight set is already reconciled by dispatch's failure paths
and housekeeping's timeouts, so a reservation can never leak — it
dies with the row's status. All amounts are billed money: 2 dp,
Decimal-bound.
"""

import uuid
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def billed_minutes(duration_sec: int | None) -> int:
    """The minute count shown on ledger lines and the overview KPI —
    started minutes, so a 61s call shows as 2. Money is NOT this:
    charges are per second (call_charge)."""
    if not duration_sec or duration_sec <= 0:
        return 0
    return -(-int(duration_sec) // 60)


def call_charge(duration_sec: int | None, rate_per_min_usd: Decimal) -> Decimal:
    """What a call costs: rate x seconds / 60, half-up to the cent. Zero
    or unknown duration is zero — a completed call with no seconds had
    no conversation, and charging for it invites disputes over cents.
    A charge that rounds to $0.00 (a 1s call) is also nothing: the
    ledger's sign check forbids a zero debit, and nobody wants one."""
    if not duration_sec or duration_sec <= 0:
        return Decimal("0.00")
    exact = Decimal(str(rate_per_min_usd)) * Decimal(int(duration_sec)) / Decimal(60)
    return exact.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def per_call_reserve(rate_per_min_usd: Decimal, max_call_minutes: int) -> Decimal:
    """Worst-case hold for one in-flight call. max_call_minutes matches
    the stuck-dialling and stale-test timeouts, so no attempt can cost
    more than its hold before something reconciles it."""
    return Decimal(str(round(float(rate_per_min_usd) * max_call_minutes, 2)))


def low_balance_line(
    *, channel_allocation: int, rate_per_min_usd: Decimal, max_call_minutes: int
) -> Decimal:
    """The warn threshold is capacity-relative, not a fixed dollar
    figure: two full dialling waves of holds. Below it the owner is
    within sight of the hard pause."""
    waves = 2 * max(int(channel_allocation), 1)
    return Decimal(
        str(round(float(per_call_reserve(rate_per_min_usd, max_call_minutes)) * waves, 2))
    )


async def balance(session: AsyncSession, *, client_account_id: uuid.UUID) -> Decimal:
    """The cached balance. Cheap; the ledger is the truth (verify())."""
    value = (
        await session.execute(
            text("SELECT wallet_balance_usd FROM client_account WHERE id = :cid"),
            {"cid": str(client_account_id)},
        )
    ).scalar_one_or_none()
    return Decimal(value) if value is not None else Decimal("0")


async def ledger_balance(session: AsyncSession, *, client_account_id: uuid.UUID) -> Decimal:
    row = (
        await session.execute(
            text(
                "SELECT coalesce(sum(amount_usd), 0) FROM wallet_ledger"
                " WHERE client_account_id = :cid"
            ),
            {"cid": str(client_account_id)},
        )
    ).scalar_one()
    return Decimal(row)


async def verify(session: AsyncSession, *, client_account_id: uuid.UUID) -> bool:
    """Cache == ledger. A False here is a bug, never a business state."""
    cached = await balance(session, client_account_id=client_account_id)
    return cached == await ledger_balance(session, client_account_id=client_account_id)


async def reserved(
    session: AsyncSession, *, client_account_id: uuid.UUID, max_call_minutes: int
) -> Decimal:
    """Sum of holds for every in-flight attempt, each at its own
    snapshotted rate (falling back to the client's current rate for
    rows claimed before the wallet existed)."""
    row = (
        await session.execute(
            text(
                """
                SELECT coalesce((
                        SELECT sum(coalesce(k.rate_per_min_usd, ca.rate_per_min_usd))
                          FROM call k WHERE k.client_account_id = ca.id
                           AND k.status = 'dialling'), 0)
                     + coalesce((
                        SELECT sum(coalesce(t.rate_per_min_usd, ca.rate_per_min_usd))
                          FROM test_run t WHERE t.client_account_id = ca.id
                           AND t.status = 'dialling'), 0)
                  FROM client_account ca WHERE ca.id = :cid
                """
            ),
            {"cid": str(client_account_id)},
        )
    ).scalar_one_or_none()
    if row is None:
        return Decimal("0")
    return Decimal(str(round(float(row) * max_call_minutes, 2)))


async def available(
    session: AsyncSession, *, client_account_id: uuid.UUID, max_call_minutes: int
) -> Decimal:
    return await balance(session, client_account_id=client_account_id) - await reserved(
        session, client_account_id=client_account_id, max_call_minutes=max_call_minutes
    )


async def credit(
    session: AsyncSession,
    *,
    client_account_id: uuid.UUID,
    amount_usd: Decimal,
    staff_id: uuid.UUID,
    note: str,
) -> uuid.UUID | None:
    """Staff top-up after a bank transfer. Returns the ledger entry id,
    or None if the client does not exist. Caller audits."""
    return await _post(
        session,
        client_account_id=client_account_id,
        entry_type="credit",
        amount_usd=amount_usd,
        note=note,
        created_by=staff_id,
    )


async def adjust(
    session: AsyncSession,
    *,
    client_account_id: uuid.UUID,
    amount_usd: Decimal,
    staff_id: uuid.UUID,
    note: str,
) -> uuid.UUID | None:
    """Signed correction — the append-only ledger's only instrument for
    fixing a wrong top-up or granting goodwill. The note is mandatory
    (enforced at the route); the ledger is not the place to be terse."""
    return await _post(
        session,
        client_account_id=client_account_id,
        entry_type="adjustment",
        amount_usd=amount_usd,
        note=note,
        created_by=staff_id,
    )


async def _post(
    session: AsyncSession,
    *,
    client_account_id: uuid.UUID,
    entry_type: str,
    amount_usd: Decimal,
    note: str,
    created_by: uuid.UUID,
) -> uuid.UUID | None:
    exact = Decimal(str(round(float(amount_usd), 2)))
    entry_id = (
        await session.execute(
            text(
                """
                INSERT INTO wallet_ledger (client_account_id, entry_type,
                                           amount_usd, note, created_by)
                SELECT :cid, :etype, :amt, :note, :actor
                 WHERE EXISTS (SELECT 1 FROM client_account WHERE id = :cid)
             RETURNING id
                """
            ),
            {
                "cid": str(client_account_id),
                "etype": entry_type,
                "amt": exact,
                "note": note,
                "actor": str(created_by),
            },
        )
    ).scalar_one_or_none()
    if entry_id is None:
        return None
    await _move_cache(session, client_account_id=client_account_id, delta=exact)
    return uuid.UUID(str(entry_id))


async def settle_call(session: AsyncSession, *, call_id: uuid.UUID) -> Decimal | None:
    """Debit a completed run call at its snapshotted rate. Returns the
    (negative) amount, or None when nothing was billed — zero duration,
    unknown call, or already settled (the ON CONFLICT swallow). Safe
    under webhook redelivery: the cache moves only when the insert
    actually inserted."""
    row = (
        await session.execute(
            text(
                """
                SELECT k.client_account_id, k.duration_sec, k.idempotency_key,
                       coalesce(k.rate_per_min_usd, ca.rate_per_min_usd)
                  FROM call k JOIN client_account ca ON ca.id = k.client_account_id
                 WHERE k.id = :id
                """
            ),
            {"id": str(call_id)},
        )
    ).first()
    if row is None:
        return None
    client_id, duration_sec, idem_key, rate = row
    return await _settle(
        session,
        client_account_id=client_id,
        entry_type="debit_call",
        key_column="call_id",
        key_value=str(call_id),
        duration_sec=duration_sec,
        rate=Decimal(rate),
        idempotency_key=idem_key,
    )


async def settle_test_call(session: AsyncSession, *, test_run_id: uuid.UUID) -> Decimal | None:
    """Same shape as settle_call, keyed on test_run.id. Test calls are
    billed like any call (the test screen has said so all along; now
    it is true)."""
    row = (
        await session.execute(
            text(
                """
                SELECT t.client_account_id, t.duration_sec,
                       coalesce(t.rate_per_min_usd, ca.rate_per_min_usd)
                  FROM test_run t JOIN client_account ca ON ca.id = t.client_account_id
                 WHERE t.id = :id
                """
            ),
            {"id": str(test_run_id)},
        )
    ).first()
    if row is None:
        return None
    client_id, duration_sec, rate = row
    return await _settle(
        session,
        client_account_id=client_id,
        entry_type="debit_test_call",
        key_column="test_run_id",
        key_value=str(test_run_id),
        duration_sec=duration_sec,
        rate=Decimal(rate),
        idempotency_key=None,
    )


async def _settle(
    session: AsyncSession,
    *,
    client_account_id: uuid.UUID,
    entry_type: str,
    key_column: str,
    key_value: str,
    duration_sec: int | None,
    rate: Decimal,
    idempotency_key: str | None,
) -> Decimal | None:
    charge = call_charge(duration_sec, rate)
    if charge <= 0:
        return None
    amount = -charge
    minutes = billed_minutes(duration_sec)  # display only; never the price
    # key_column is one of two literals chosen above, never user input.
    entry_id = (
        await session.execute(
            text(
                f"""
                INSERT INTO wallet_ledger (client_account_id, entry_type, amount_usd,
                                           {key_column}, duration_sec, billed_minutes,
                                           rate_per_min_usd, idempotency_key, created_by)
                VALUES (:cid, :etype, :amt, :key, :dur, :mins, :rate, :idem,
                        '00000000-0000-0000-0000-000000000000')
             ON CONFLICT ({key_column}) WHERE {key_column} IS NOT NULL DO NOTHING
             RETURNING id
                """  # noqa: S608 — key_column is a hardcoded literal
            ),
            {
                "cid": str(client_account_id),
                "etype": entry_type,
                "amt": amount,
                "key": key_value,
                "dur": int(duration_sec or 0),
                "mins": minutes,
                "rate": Decimal(str(round(float(rate), 2))),
                "idem": idempotency_key,
            },
        )
    ).scalar_one_or_none()
    if entry_id is None:
        return None  # already settled — redelivery, or a replayed poll
    await _move_cache(session, client_account_id=client_account_id, delta=amount)
    return amount


async def _move_cache(
    session: AsyncSession, *, client_account_id: uuid.UUID, delta: Decimal
) -> None:
    # Single atomic read-modify-write; the row lock it takes is the
    # same one both reserve gates hold FOR UPDATE, so cache and ledger
    # can never be observed mid-divergence by a balance check.
    await session.execute(
        text(
            "UPDATE client_account SET wallet_balance_usd = wallet_balance_usd + :d WHERE id = :cid"
        ),
        {"d": delta, "cid": str(client_account_id)},
    )


async def set_rate(
    session: AsyncSession, *, client_account_id: uuid.UUID, rate_per_min_usd: Decimal
) -> tuple[str, bool] | None:
    """Returns (client name, changed). changed=False means the rate was
    already exactly this — the caller notifies the client only on an
    actual change (silent repricing is the one thing a prepaid model
    must never do; so is a notification for a no-op save)."""
    exact = Decimal(str(round(float(rate_per_min_usd), 2)))
    row = (
        await session.execute(
            text(
                "UPDATE client_account SET rate_per_min_usd = :r"
                " WHERE id = :cid AND rate_per_min_usd IS DISTINCT FROM :r"
                " RETURNING name"
            ),
            {"r": exact, "cid": str(client_account_id)},
        )
    ).scalar_one_or_none()
    if row is not None:
        return str(row), True
    name = (
        await session.execute(
            text("SELECT name FROM client_account WHERE id = :cid"),
            {"cid": str(client_account_id)},
        )
    ).scalar_one_or_none()
    if name is None:
        return None
    return str(name), False


async def ledger_page(
    session: AsyncSession, *, client_account_id: uuid.UUID, limit: int = 100, offset: int = 0
) -> list[dict[str, Any]]:
    """Newest first, per-call line items — price legibility is the whole
    point of the flat rate, so nothing is rolled up. Joined agent names
    survive agent deletion as NULL (the ledger row outlives everything)."""
    rows = (
        await session.execute(
            text(
                """
                SELECT wl.created_at, wl.entry_type, wl.amount_usd,
                       wl.billed_minutes, wl.note,
                       coalesce(ac.name, at.name)
                  FROM wallet_ledger wl
                  LEFT JOIN call k ON k.id = wl.call_id
                  LEFT JOIN agent ac ON ac.id = k.agent_id
                  LEFT JOIN test_run t ON t.id = wl.test_run_id
                  LEFT JOIN agent at ON at.id = t.agent_id
                 WHERE wl.client_account_id = :cid
                 ORDER BY wl.created_at DESC, wl.id
                 LIMIT :lim OFFSET :off
                """
            ),
            {"cid": str(client_account_id), "lim": int(limit), "off": int(offset)},
        )
    ).all()
    return [
        {
            "created_at": r[0],
            "entry_type": r[1],
            "amount": float(r[2]),
            "billed_minutes": r[3],
            "note": r[4],
            "agent_name": r[5],
        }
        for r in rows
    ]


async def console_overview(session: AsyncSession) -> list[dict[str, Any]]:
    """Per client, the console's money row: balance, rate, billed MTD
    (wallet debits), Telnyx cost MTD (the margin monitor — cost sync's
    surviving job), and the margin between them. Test-call Telnyx cost
    is not joinable to cost_actual, so the cost column understates by
    test traffic — the billed column includes it, so margin reads
    slightly generous; recorded, acceptable."""
    rows = (
        await session.execute(
            text(
                """
                SELECT c.id, c.name, c.wallet_balance_usd, c.rate_per_min_usd,
                       c.channel_allocation,
                       coalesce((SELECT -sum(wl.amount_usd) FROM wallet_ledger wl
                          WHERE wl.client_account_id = c.id
                            AND wl.entry_type IN ('debit_call', 'debit_test_call')
                            AND wl.created_at >= date_trunc('month', now())), 0),
                       coalesce((SELECT sum(k.cost_actual) FROM call k
                          WHERE k.client_account_id = c.id
                            AND k.cost_actual IS NOT NULL
                            AND k.started_at >= date_trunc('month', now())), 0),
                       coalesce((SELECT sum(wl.amount_usd) FROM wallet_ledger wl
                          WHERE wl.client_account_id = c.id
                            AND wl.entry_type = 'credit'), 0)
                  FROM client_account c ORDER BY c.name
                """
            )
        )
    ).all()
    return [
        {
            "id": str(r[0]),
            "name": r[1],
            "balance": float(r[2]),
            "rate_per_min": float(r[3]),
            "channel_allocation": int(r[4]),
            "billed_mtd": float(r[5]),
            "cost_mtd": float(r[6]),
            "margin_mtd": round(float(r[5]) - float(r[6]), 4),
            "credited_total": float(r[7]),
        }
        for r in rows
    ]
