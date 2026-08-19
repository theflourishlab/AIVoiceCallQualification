"""Client plane: the wallet (14 Aug 2026; supersedes the FR-BILL-6
screen this file used to be).

What a client sees: their balance, the flat per-minute rate stated
plainly (rounded up to the next started minute — minutes are shown now,
because a public flat rate means they no longer derive any margin),
every ledger line, where to pay, and their pre-wallet invoices as
receipts. What they still never see: Telnyx cost or Becca's margin.

RLS scopes every query to the requesting client's account.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

from becca.services import wallet
from becca.web.deps import ClientContext, require_client_context

router = APIRouter()

VISIBLE_STATUSES = ("sent", "paid", "overdue")
PAGE_SIZE = 50


@router.get("/wallet", response_class=HTMLResponse)
async def wallet_view(
    request: Request, ctx: Annotated[ClientContext, Depends(require_client_context)]
) -> HTMLResponse:
    settings = request.app.state.settings
    try:
        page = max(1, int(request.query_params.get("page", "1")))
    except ValueError:
        page = 1
    async with request.app.state.db.client_session(ctx.client_account_id) as s:
        account = (
            await s.execute(
                text(
                    "SELECT wallet_balance_usd, rate_per_min_usd, channel_allocation"
                    " FROM client_account WHERE id = :cid"
                ),
                {"cid": str(ctx.client_account_id)},
            )
        ).one()
        entries = await wallet.ledger_page(
            s,
            client_account_id=ctx.client_account_id,
            limit=PAGE_SIZE,
            offset=(page - 1) * PAGE_SIZE,
        )
        invoices = (
            await s.execute(
                text(
                    "SELECT id, period, amount, status, paid_at FROM invoice"
                    " WHERE status = ANY(:visible) ORDER BY period DESC"
                ),
                {"visible": list(VISIBLE_STATUSES)},
            )
        ).all()

    balance, rate, allocation = float(account[0]), float(account[1]), int(account[2])
    low_line = float(
        wallet.low_balance_line(
            channel_allocation=allocation,
            rate_per_min_usd=account[1],
            max_call_minutes=settings.max_call_minutes,
        )
    )
    result: HTMLResponse = request.app.state.templates.TemplateResponse(
        request,
        "client/wallet.html",
        {
            "plane": "client",
            "ctx": ctx,
            "nav_active": "wallet",
            "balance": balance,
            "rate_per_min": rate,
            "low": balance < low_line,
            "negative": balance < 0,
            "entries": entries,
            "page": page,
            "has_next": len(entries) == PAGE_SIZE,
            "bank_details": settings.bank_transfer_details,
            "receipts": [
                {
                    "id": r[0],
                    "period": r[1],
                    "amount": float(r[2]),
                    "status": r[3],
                    "paid_at": r[4],
                }
                for r in invoices
            ],
        },
    )
    return result


@router.get("/billing")
async def billing_redirect(
    request: Request, ctx: Annotated[ClientContext, Depends(require_client_context)]
) -> Response:
    """Old bookmarks and notification links land on the wallet."""
    return RedirectResponse("/wallet", status_code=308)


@router.get("/billing/invoices/{invoice_id}/pdf")
async def invoice_pdf(
    request: Request,
    ctx: Annotated[ClientContext, Depends(require_client_context)],
    invoice_id: uuid.UUID,
) -> Response:
    """The stored bytes, RLS-scoped: another client's invoice id is
    simply not found. Drafts stay Becca-internal. Receipts, since the
    wallet — the URL survives the rename so old links keep working."""
    async with request.app.state.db.client_session(ctx.client_account_id) as s:
        row = (
            await s.execute(
                text("SELECT period, pdf FROM invoice WHERE id = :iid AND status = ANY(:visible)"),
                {"iid": str(invoice_id), "visible": list(VISIBLE_STATUSES)},
            )
        ).first()
    if row is None:
        return Response("Not found", status_code=404)
    return Response(
        content=bytes(row[1]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="becca-invoice-{row[0]}.pdf"'},
    )
