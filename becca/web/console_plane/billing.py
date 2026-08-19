"""Console: wallets, rates and the margin monitor (wallet, 14 Aug 2026).

The money screen. Clients prepay: staff credit each wallet here after a
bank transfer, calls debit it per minute, and this screen watches the
difference between what was billed (the ledger) and what Telnyx charged
(cost sync — its surviving job). Costs are synced fresh on every render;
legacy invoices remain readable as receipts and can still be marked
paid, but nothing generates new ones.
"""

import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from becca.services import audit, billing, notify, wallet
from becca.services import console as console_service
from becca.web.deps import StaffContext, check_csrf_form, require_staff

router = APIRouter()


@router.get("/billing", response_class=HTMLResponse)
async def billing_screen(
    request: Request, staff: Annotated[StaffContext, Depends(require_staff)]
) -> HTMLResponse:
    gateway = request.app.state.telnyx
    synced = True
    async with request.app.state.db.console_session() as s:
        try:
            await billing.sync_costs(s, gateway)
        except Exception:
            synced = False
        balance = await console_service.balance_overview(s, gateway)
        wallets = await wallet.console_overview(s)
        invoices = await billing.list_invoices(s)
    result: HTMLResponse = request.app.state.templates.TemplateResponse(
        request,
        "console/billing.html",
        {
            "plane": "console",
            "staff": staff,
            "synced": synced,
            "balance": balance,
            "wallets": wallets,
            "billed_mtd": sum(w["billed_mtd"] for w in wallets),
            "cost_mtd": sum(w["cost_mtd"] for w in wallets),
            "invoices": invoices,
            "error": request.query_params.get("error"),
            "done": request.query_params.get("done"),
        },
    )
    return result


@router.get("/billing/wallet/{client_id}", response_class=HTMLResponse)
async def wallet_ledger_screen(
    request: Request,
    staff: Annotated[StaffContext, Depends(require_staff)],
    client_id: uuid.UUID,
) -> Response:
    async with request.app.state.db.console_session() as s:
        overview = [w for w in await wallet.console_overview(s) if w["id"] == str(client_id)]
        if not overview:
            return RedirectResponse("/billing?error=missing", status_code=303)
        entries = await wallet.ledger_page(s, client_account_id=client_id, limit=200)
    result: HTMLResponse = request.app.state.templates.TemplateResponse(
        request,
        "console/wallet_ledger.html",
        {
            "plane": "console",
            "staff": staff,
            "client": overview[0],
            "entries": entries,
        },
    )
    return result


@router.post("/billing/credit/{client_id}", dependencies=[Depends(check_csrf_form)])
async def credit_wallet(
    request: Request,
    staff: Annotated[StaffContext, Depends(require_staff)],
    client_id: uuid.UUID,
    amount_usd: Annotated[float, Form()],
    note: Annotated[str, Form()] = "",
) -> RedirectResponse:
    """The funding path: a bank transfer landed, staff credit the wallet.
    No payment gateway by design — the note carries the transfer ref."""
    if not 0 < amount_usd <= 100_000:
        return RedirectResponse("/billing?error=amount", status_code=303)
    async with request.app.state.db.console_session() as s:
        entry = await wallet.credit(
            s,
            client_account_id=client_id,
            amount_usd=Decimal(str(round(amount_usd, 2))),
            staff_id=staff.staff_id,
            note=note.strip(),
        )
        if entry is None:
            return RedirectResponse("/billing?error=missing", status_code=303)
        await audit.record(
            s,
            actor_type="staff",
            actor_id=staff.staff_id,
            action="credited_wallet",
            client_account_id=client_id,
            target=str(client_id),
            meta={"amount": round(amount_usd, 2), "note": note.strip()[:200]},
        )
        await notify.emit(
            s,
            event="wallet_credited",
            title=notify.CLIENT_EVENTS["wallet_credited"],
            body=f"${amount_usd:,.2f} was added to your wallet.",
            client_account_id=client_id,
        )
    return RedirectResponse("/billing?done=credited", status_code=303)


@router.post("/billing/adjust/{client_id}", dependencies=[Depends(check_csrf_form)])
async def adjust_wallet(
    request: Request,
    staff: Annotated[StaffContext, Depends(require_staff)],
    client_id: uuid.UUID,
    amount_usd: Annotated[float, Form()],
    note: Annotated[str, Form()] = "",
) -> RedirectResponse:
    """The append-only ledger's correction instrument: signed, and the
    note is mandatory — an unexplained adjustment is worse than none."""
    if amount_usd == 0 or not -100_000 <= amount_usd <= 100_000:
        return RedirectResponse("/billing?error=amount", status_code=303)
    if not note.strip():
        return RedirectResponse("/billing?error=note", status_code=303)
    async with request.app.state.db.console_session() as s:
        entry = await wallet.adjust(
            s,
            client_account_id=client_id,
            amount_usd=Decimal(str(round(amount_usd, 2))),
            staff_id=staff.staff_id,
            note=note.strip(),
        )
        if entry is None:
            return RedirectResponse("/billing?error=missing", status_code=303)
        await audit.record(
            s,
            actor_type="staff",
            actor_id=staff.staff_id,
            action="adjusted_wallet",
            client_account_id=client_id,
            target=str(client_id),
            meta={"amount": round(amount_usd, 2), "note": note.strip()[:200]},
        )
    return RedirectResponse("/billing?done=adjusted", status_code=303)


@router.post("/billing/rate/{client_id}", dependencies=[Depends(check_csrf_form)])
async def set_rate(
    request: Request,
    staff: Annotated[StaffContext, Depends(require_staff)],
    client_id: uuid.UUID,
    rate_per_min: Annotated[float, Form()],
) -> RedirectResponse:
    """Per-client flat rate. In-flight and queued calls keep the rate
    snapshotted when they were claimed; only future claims see this.
    The client is told on an actual change — never silently repriced."""
    if not 0.01 <= rate_per_min <= 10:
        return RedirectResponse("/billing?error=rate", status_code=303)
    async with request.app.state.db.console_session() as s:
        result = await wallet.set_rate(
            s, client_account_id=client_id, rate_per_min_usd=Decimal(str(round(rate_per_min, 2)))
        )
        if result is None:
            return RedirectResponse("/billing?error=missing", status_code=303)
        name, changed = result
        if changed:
            await audit.record(
                s,
                actor_type="staff",
                actor_id=staff.staff_id,
                action="set_rate_per_min",
                client_account_id=client_id,
                target=name,
                meta={"rate_per_min": round(rate_per_min, 2)},
            )
            await notify.emit(
                s,
                event="rate_changed",
                title=notify.CLIENT_EVENTS["rate_changed"],
                body=f"Calls are now billed at ${rate_per_min:.2f} per minute"
                " (rounded up to the next started minute).",
                client_account_id=client_id,
            )
    return RedirectResponse("/billing?done=rate", status_code=303)


@router.post("/billing/invoices/{invoice_id}/mark", dependencies=[Depends(check_csrf_form)])
async def mark_invoice(
    request: Request,
    staff: Annotated[StaffContext, Depends(require_staff)],
    invoice_id: uuid.UUID,
    status: Annotated[str, Form()],
) -> RedirectResponse:
    """Legacy receipts keep their manual lifecycle — a pre-wallet invoice
    that gets paid should still be markable."""
    async with request.app.state.db.console_session() as s:
        moved = await billing.mark_invoice(s, invoice_id=invoice_id, status=status)
        if not moved:
            return RedirectResponse("/billing?error=transition", status_code=303)
        await audit.record(
            s,
            actor_type="staff",
            actor_id=staff.staff_id,
            action=f"marked_invoice_{status}",
            target=str(invoice_id),
        )
    return RedirectResponse("/billing?done=marked", status_code=303)


@router.get("/billing/invoices/{invoice_id}/pdf")
async def invoice_pdf(
    request: Request,
    staff: Annotated[StaffContext, Depends(require_staff)],
    invoice_id: uuid.UUID,
) -> Response:
    async with request.app.state.db.console_session() as s:
        found = await billing.invoice_pdf(s, invoice_id=invoice_id)
    if found is None:
        return Response("Not found", status_code=404)
    filename, pdf = found
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )
