"""Console: numbers & capacity (FR-CONSOLE-3/5/6).

Inventory facts sync from Telnyx when the screen renders; the assignment
column is ours. Every mutation writes an audit row (FR-CONSOLE-8).
"""

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

from becca.services import audit, billing
from becca.services import console as console_service
from becca.services.console import AllocationExceeded, ReassignmentRefused
from becca.telnyx.gateway import TelnyxError
from becca.web.deps import StaffContext, check_csrf_form, require_staff

router = APIRouter()


@router.get("/numbers", response_class=HTMLResponse)
async def numbers_screen(
    request: Request, staff: Annotated[StaffContext, Depends(require_staff)]
) -> HTMLResponse:
    settings = request.app.state.settings
    gateway = request.app.state.telnyx
    async with request.app.state.db.console_session() as s:
        synced = await console_service.sync_numbers(s, gateway)
        inventory = await console_service.list_inventory(s)
        split = await console_service.allocation_split(s, ceiling=settings.channel_ceiling)
        note = await console_service.account_note(s)
        missing_groups = await billing.clients_missing_groups(s)
    dests = await console_service.destinations(gateway)
    result: HTMLResponse = request.app.state.templates.TemplateResponse(
        request,
        "console/numbers.html",
        {
            "plane": "console",
            "staff": staff,
            "inventory": inventory,
            "synced": synced,
            "split": split,
            "note": note,
            "destinations": dests,
            "missing_groups": missing_groups,
            "error": request.query_params.get("error"),
            "done": request.query_params.get("done"),
            "warn": request.query_params.get("warn"),
        },
    )
    return result


@router.post("/numbers/order", dependencies=[Depends(check_csrf_form)])
async def order_number(
    request: Request, staff: Annotated[StaffContext, Depends(require_staff)]
) -> RedirectResponse:
    async with request.app.state.db.console_session() as s:
        try:
            e164 = await console_service.order_number(s, request.app.state.telnyx)
        except TelnyxError:
            return RedirectResponse("/numbers?error=order", status_code=303)
        await audit.record(
            s,
            actor_type="staff",
            actor_id=staff.staff_id,
            action="ordered_number",
            target=e164,
        )
    return RedirectResponse("/numbers?done=ordered", status_code=303)


@router.post("/numbers/{number_id}/assign", dependencies=[Depends(check_csrf_form)])
async def assign_number(
    request: Request,
    staff: Annotated[StaffContext, Depends(require_staff)],
    number_id: uuid.UUID,
    client_id: Annotated[str, Form()] = "",
) -> RedirectResponse:
    target_client = uuid.UUID(client_id) if client_id else None
    async with request.app.state.db.console_session() as s:
        e164 = (
            await s.execute(
                text("SELECT phone_e164 FROM phone_number WHERE id = :nid"),
                {"nid": str(number_id)},
            )
        ).scalar_one_or_none()
        if e164 is None:
            return RedirectResponse("/numbers?error=missing", status_code=303)
        try:
            await console_service.assign_number(
                s, phone_number_id=number_id, client_account_id=target_client
            )
        except ReassignmentRefused:
            # FR-CONSOLE-5: the number is a dialling run's caller ID.
            return RedirectResponse("/numbers?error=dialling", status_code=303)
        await audit.record(
            s,
            actor_type="staff",
            actor_id=staff.staff_id,
            action="assigned_number" if target_client else "unassigned_number",
            client_account_id=target_client,
            target=e164,
        )
        # FR-BILL-3 rides the assignment: from this moment the number's
        # costs are this client's, so the billing group must exist and
        # hold the number BEFORE the first billed run. A Telnyx failure
        # degrades to a visible warning — the assignment itself stands.
        warn = ""
        try:
            if target_client is not None:
                group_id = await billing.ensure_billing_group(
                    s, request.app.state.telnyx, client_account_id=target_client
                )
                await audit.record(
                    s,
                    actor_type="staff",
                    actor_id=staff.staff_id,
                    action="ensured_billing_group",
                    client_account_id=target_client,
                    target=e164,
                    meta={"billing_group_id": group_id},
                )
            else:
                await billing.detach_number(s, request.app.state.telnyx, phone_number_id=number_id)
        except Exception:
            warn = "&warn=billing_group"
    return RedirectResponse(f"/numbers?done=assigned{warn}", status_code=303)


@router.post("/billing-groups/ensure", dependencies=[Depends(check_csrf_form)])
async def ensure_billing_groups(
    request: Request, staff: Annotated[StaffContext, Depends(require_staff)]
) -> RedirectResponse:
    """Backfill for numbers assigned before billing groups shipped
    (FR-BILL-3: groups must exist before the first billed run)."""
    async with request.app.state.db.console_session() as s:
        missing = await billing.clients_missing_groups(s)
        for client in missing:
            try:
                group_id = await billing.ensure_billing_group(
                    s, request.app.state.telnyx, client_account_id=uuid.UUID(client["id"])
                )
            except Exception:
                return RedirectResponse("/numbers?warn=billing_group", status_code=303)
            await audit.record(
                s,
                actor_type="staff",
                actor_id=staff.staff_id,
                action="ensured_billing_group",
                client_account_id=uuid.UUID(client["id"]),
                target=client["name"],
                meta={"billing_group_id": group_id},
            )
    return RedirectResponse("/numbers?done=groups", status_code=303)


@router.post("/allocation/{client_id}", dependencies=[Depends(check_csrf_form)])
async def set_allocation(
    request: Request,
    staff: Annotated[StaffContext, Depends(require_staff)],
    client_id: uuid.UUID,
    channels: Annotated[int, Form()],
) -> RedirectResponse:
    settings = request.app.state.settings
    async with request.app.state.db.console_session() as s:
        name = (
            await s.execute(
                text("SELECT name FROM client_account WHERE id = :cid"),
                {"cid": str(client_id)},
            )
        ).scalar_one_or_none()
        if name is None:
            return RedirectResponse("/numbers?error=missing", status_code=303)
        try:
            await console_service.set_allocation(
                s,
                client_account_id=client_id,
                channels=channels,
                ceiling=settings.channel_ceiling,
            )
        except AllocationExceeded:
            return RedirectResponse("/numbers?error=ceiling", status_code=303)
        await audit.record(
            s,
            actor_type="staff",
            actor_id=staff.staff_id,
            action="set_channel_allocation",
            client_account_id=client_id,
            target=name,
            meta={"channels": channels},
        )
    return RedirectResponse("/numbers?done=allocated", status_code=303)


@router.post("/account-note", dependencies=[Depends(check_csrf_form)])
async def save_account_note(
    request: Request,
    staff: Annotated[StaffContext, Depends(require_staff)],
    verification_tier: Annotated[str, Form()] = "",
    doc_expiry: Annotated[str, Form()] = "",
) -> RedirectResponse:
    """The health facts Telnyx's API cannot report — staff-recorded, with
    provenance shown on the screen (FR-CONSOLE-6)."""
    expiry = date.fromisoformat(doc_expiry) if doc_expiry else None
    async with request.app.state.db.console_session() as s:
        await console_service.save_account_note(
            s,
            verification_tier=verification_tier,
            doc_expiry=expiry,
            staff_id=staff.staff_id,
        )
        await audit.record(
            s,
            actor_type="staff",
            actor_id=staff.staff_id,
            action="updated_account_note",
            meta={"verification_tier": verification_tier, "doc_expiry": doc_expiry or None},
        )
    return RedirectResponse("/numbers?done=noted", status_code=303)
