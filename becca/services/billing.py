"""Telnyx cost attribution — since the wallet (14 Aug 2026), Becca's
INTERNAL margin monitor, never client billing.

Clients are billed by services/wallet.py at a flat per-minute rate;
this module's cost sync fills call.cost_actual from detail records so
the console can watch billed-vs-cost per client. The invoice functions
below are frozen history: pre-wallet invoices stay readable (and
markable paid) as receipts, but nothing generates new ones.

FR-BILL-3 (billing groups) survives unchanged: one Telnyx billing group
per client account, attached to that client's numbers, so cost
attribution returns natively on the detail record. The enforcement
point is number assignment (ensure_billing_group runs there;
idempotent re-attachment).
"""

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from becca.telnyx.gateway import TelnyxGateway

# The per-call billable record types and their session-id key — the key
# VARIES BY TYPE (live-verified, spike findings §11). sip-trunking is
# the PSTN leg and the LARGEST single component ($0.117/min to Nigerian
# mobiles vs $0.05/min conversational AI) — omitting it undercounted
# true cost ~4x (caught reconciling the 12 Aug balance, §11a).
# Conversational tts/stt carry no cost (bundled in the per-minute AI
# rate); inference joins to nothing and is account-level overhead,
# absorbed by FR-BILL-9's buffered estimates; number MRC ($35/mo each,
# live-verified) never appears in detail records at all — it enters at
# invoice time, not per call.
JOINABLE_RECORD_TYPES = {
    "ai-voice-assistant": "telnyx_session_id",
    "sip-trunking": "telnyx_session_id",
    "call-control": "telnyx_session_id",
    "amd": "call_session_id",
    "recording": "telnyx_session_id",
    "noise-suppression": "call_session_id",
    "media_storage": "telnyx_session_id",
}
# Telnyx silently caps page[size] at 50 but reports honest total_pages
# (live-verified), so the loop stays correct — 100 pages = 5000 records
# per type per sync, a generous ceiling for a single-client agency and
# cheap insurance against an unbounded loop. When volume grows, add
# filter[started_at][gte] (verified to work) rather than raising this.
MAX_PAGES = 100


async def ensure_billing_group(
    session: AsyncSession, gateway: TelnyxGateway, *, client_account_id: uuid.UUID
) -> str | None:
    """Create the client's billing group if missing, then attach every
    Telnyx-synced number they hold. Returns the group id, or None if the
    client does not exist. Telnyx errors propagate — the caller decides
    whether a failure blocks or degrades to a visible warning."""
    row = (
        await session.execute(
            text("SELECT name, telnyx_billing_group_id FROM client_account WHERE id = :cid"),
            {"cid": str(client_account_id)},
        )
    ).first()
    if row is None:
        return None
    group_id = row[1]
    if not group_id:
        # The name is for the Telnyx portal; the id is what matters.
        group_id = await gateway.create_billing_group(name=f"client — {row[0]}")
        await session.execute(
            text("UPDATE client_account SET telnyx_billing_group_id = :g WHERE id = :cid"),
            {"g": group_id, "cid": str(client_account_id)},
        )
    numbers = (
        (
            await session.execute(
                text(
                    "SELECT telnyx_phone_number_id FROM phone_number"
                    " WHERE client_account_id = :cid AND telnyx_phone_number_id IS NOT NULL"
                ),
                {"cid": str(client_account_id)},
            )
        )
        .scalars()
        .all()
    )
    for telnyx_id in numbers:
        await gateway.set_number_billing_group(
            telnyx_phone_number_id=str(telnyx_id), billing_group_id=str(group_id)
        )
    return str(group_id)


async def detach_number(
    session: AsyncSession, gateway: TelnyxGateway, *, phone_number_id: uuid.UUID
) -> None:
    """Clear the billing group on an unassigned number, so a later
    tenant's costs cannot land in the previous client's group."""
    telnyx_id = (
        await session.execute(
            text("SELECT telnyx_phone_number_id FROM phone_number WHERE id = :nid"),
            {"nid": str(phone_number_id)},
        )
    ).scalar_one_or_none()
    if telnyx_id:
        await gateway.set_number_billing_group(
            telnyx_phone_number_id=str(telnyx_id), billing_group_id=None
        )


async def sync_costs(session: AsyncSession, gateway: TelnyxGateway) -> int:
    """FR-BILL-2/4: fill call.cost_actual from detail records, joined on
    the per-type session-id key, AMD included. Idempotent — costs are a
    full recompute per session, so a record that arrived late simply
    raises the total on the next sync. Returns rows updated.

    Telnyx errors propagate; the caller decides how to degrade (the
    console runs this alongside the hourly balance snapshot and just
    tries again next hour)."""
    totals: dict[str, float] = {}
    for record_type, key in JOINABLE_RECORD_TYPES.items():
        page = 1
        while page <= MAX_PAGES:
            body = await gateway.list_detail_records(record_type=record_type, page_number=page)
            for r in body.get("data", []):
                sid = r.get(key) or r.get("call_session_id") or r.get("telnyx_session_id")
                cost = float(r.get("cost") or 0)
                if sid and cost:
                    totals[str(sid)] = totals.get(str(sid), 0.0) + cost
            meta = body.get("meta") or {}
            if page >= int(meta.get("total_pages") or 1):
                break
            page += 1
    updated = 0
    for sid, total in totals.items():
        # Decimal, not float: a float binds with its full binary
        # expansion and never compares equal to the stored numeric,
        # which would make every sync rewrite every row.
        exact = Decimal(str(round(total, 4)))
        rows = (
            await session.execute(
                text(
                    "UPDATE call SET cost_actual = :c"
                    " WHERE telnyx_call_session_id = :sid"
                    " AND cost_actual IS DISTINCT FROM :c"
                    " RETURNING 1"
                ),
                {"c": exact, "sid": sid},
            )
        ).all()
        updated += len(rows)
    return updated


async def list_invoices(session: AsyncSession) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            text(
                """
                SELECT i.id, c.name, i.period, i.telnyx_cost, i.number_mrc,
                       i.margin_pct, i.amount, i.status, i.created_at
                  FROM invoice i JOIN client_account c ON c.id = i.client_account_id
                 ORDER BY i.period DESC, c.name
                """
            )
        )
    ).all()
    return [
        {
            "id": r[0],
            "client": r[1],
            "period": r[2],
            "telnyx_cost": float(r[3]),
            "number_mrc": float(r[4]),
            "margin_pct": float(r[5]),
            "amount": float(r[6]),
            "status": r[7],
            "created_at": r[8],
        }
        for r in rows
    ]


async def mark_invoice(session: AsyncSession, *, invoice_id: uuid.UUID, status: str) -> bool:
    """Manual lifecycle (FR-BILL-7). Payment can only move forward:
    draft -> sent -> paid; a paid invoice never regresses."""
    statements = {
        "sent": (
            "UPDATE invoice SET status = 'sent', sent_at = now()"
            " WHERE id = :iid AND status IN ('draft', 'overdue') RETURNING id"
        ),
        "paid": (
            "UPDATE invoice SET status = 'paid', paid_at = now()"
            " WHERE id = :iid AND status IN ('draft', 'sent', 'overdue') RETURNING id"
        ),
    }
    if status not in statements:
        return False
    row = (
        await session.execute(text(statements[status]), {"iid": str(invoice_id)})
    ).scalar_one_or_none()
    return row is not None


async def invoice_pdf(session: AsyncSession, *, invoice_id: uuid.UUID) -> tuple[str, bytes] | None:
    """The STORED bytes — reissue is byte-identical because nothing is
    ever re-rendered."""
    row = (
        await session.execute(
            text(
                "SELECT c.name, i.period, i.pdf FROM invoice i"
                " JOIN client_account c ON c.id = i.client_account_id WHERE i.id = :iid"
            ),
            {"iid": str(invoice_id)},
        )
    ).first()
    if row is None:
        return None
    filename = f"becca-invoice-{row[0].lower().replace(' ', '-')}-{row[1]}.pdf"
    return filename, bytes(row[2])


async def clients_missing_groups(session: AsyncSession) -> list[dict[str, str]]:
    """Clients holding Telnyx-synced numbers with no billing group yet —
    assignments made before this shipped. Rendered as a backfill prompt
    on the numbers screen; empty once FR-BILL-3 is satisfied."""
    rows = (
        await session.execute(
            text(
                """
                SELECT DISTINCT c.id, c.name FROM client_account c
                  JOIN phone_number n ON n.client_account_id = c.id
                 WHERE c.telnyx_billing_group_id IS NULL
                   AND n.telnyx_phone_number_id IS NOT NULL
                 ORDER BY c.name
                """
            )
        )
    ).all()
    return [{"id": str(r[0]), "name": r[1]} for r in rows]
