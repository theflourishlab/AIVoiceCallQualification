"""Console: client accounts (FR-CONSOLE-1/2/4) and Entering (FR-ARCH-2).

The landing screen carries the balance and its projected exhaustion
persistently (FR-NOTIFY-2A): a zero balance stops every client at once
and is the one failure that is not fail-safe from their point of view.
"""

import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

from becca.services import audit, billing, notify
from becca.services import console as console_service
from becca.web.deps import StaffContext, check_csrf_form, require_staff
from becca.web.sessions import SessionStore

router = APIRouter()

# Below this many days of projected runway, the landing screen shifts
# from a figure to a warning (FR-CONSOLE-7: "well before exhaustion").
BALANCE_WARN_DAYS = 30


@router.get("/", response_class=HTMLResponse)
async def client_list(
    request: Request, staff: Annotated[StaffContext, Depends(require_staff)]
) -> HTMLResponse:
    settings = request.app.state.settings
    async with request.app.state.db.console_session() as s:
        balance = await console_service.balance_overview(s, request.app.state.telnyx)
        if balance.available and (
            balance.days_left is not None and balance.days_left < BALANCE_WARN_DAYS
        ):
            # FR-NOTIFY-2A/2B: the persistent banner above is the alert;
            # this row is the record, one per day at most.
            await notify.emit(
                s,
                event="balance_low",
                title=notify.STAFF_EVENTS["balance_low"],
                body=(
                    f"${balance.credit:.2f} left; at the current burn it runs out"
                    f" on {balance.exhaustion_date}."
                ),
                dedupe_hours=24,
            )
        if balance.snapshotted:
            # FR-BILL-2: cost sync rides the hourly snapshot cadence, so
            # the "To bill" column below reflects fresh detail records.
            # A Telnyx failure here waits for the next hour, silently —
            # the balance panel already surfaces Telnyx being down.
            try:
                await billing.sync_costs(s, request.app.state.telnyx)
            except Exception:  # noqa: S110
                pass
        clients = await console_service.client_rows(s)
        split = await console_service.allocation_split(s, ceiling=settings.channel_ceiling)
        # FR-CONSOLE-4: a checklist for every account still onboarding.
        checklists: list[dict[str, Any]] = []
        for c in clients:
            if c["status"] == "onboarding":
                items = await console_service.onboarding_checklist(s, client_account_id=c["id"])
                # key is "checks", not "items": dict.items shadows the
                # latter under Jinja attribute lookup.
                checklists.append(
                    {
                        "client": c,
                        "checks": items,
                        "done": sum(1 for i in items if i["ok"]),
                    }
                )
        note = await console_service.account_note(s)
    if note and note["doc_expiry"]:
        note["doc_days_left"] = (note["doc_expiry"] - date.today()).days
    dests = await console_service.destinations(request.app.state.telnyx)
    calls_mtd = sum(c["calls_mtd"] for c in clients)
    billed_mtd = sum(c["billed_mtd"] for c in clients)
    result: HTMLResponse = request.app.state.templates.TemplateResponse(
        request,
        "console/clients.html",
        {
            "plane": "console",
            "staff": staff,
            "clients": clients,
            "balance": balance,
            "warn_days": BALANCE_WARN_DAYS,
            "split": split,
            "checklists": checklists,
            "note": note,
            "destinations": dests,
            "kpi": {
                "active": sum(1 for c in clients if c["status"] != "onboarding"),
                "onboarding": sum(1 for c in clients if c["status"] == "onboarding"),
                "calls_mtd": calls_mtd,
                "billed_mtd": billed_mtd,
            },
        },
    )
    return result


@router.get("/clients/new", response_class=HTMLResponse)
async def new_client_form(
    request: Request, staff: Annotated[StaffContext, Depends(require_staff)]
) -> HTMLResponse:
    result: HTMLResponse = request.app.state.templates.TemplateResponse(
        request, "console/client_new.html", {"plane": "console", "staff": staff}
    )
    return result


@router.post("/clients", dependencies=[Depends(check_csrf_form)])
async def create_client(
    request: Request,
    staff: Annotated[StaffContext, Depends(require_staff)],
    name: Annotated[str, Form()],
    rate_per_min: Annotated[float, Form()],
    owner_email: Annotated[str, Form()],
) -> RedirectResponse:
    # FR-CONSOLE-2: no Telnyx object is created at this point.
    # Same bounds as the Billing rate editor.
    if not 0.01 <= rate_per_min <= 10:
        return RedirectResponse("/clients/new?error=rate", status_code=303)
    async with request.app.state.db.console_session() as s:
        # billing_entity and margin_pct are pre-wallet columns kept for
        # legacy receipts (migration 0011); nothing new reads them.
        client_id = (
            await s.execute(
                text(
                    "INSERT INTO client_account"
                    " (name, billing_entity, margin_pct, rate_per_min_usd)"
                    " VALUES (:n, :n, 0, :r) RETURNING id"
                ),
                {"n": name.strip(), "r": round(rate_per_min, 2)},
            )
        ).scalar_one()
        await s.execute(
            text(
                "INSERT INTO app_user (client_account_id, google_email, role)"
                " VALUES (:cid, :e, 'owner')"
            ),
            {"cid": str(client_id), "e": owner_email.strip().lower()},
        )
        await audit.record(
            s,
            actor_type="staff",
            actor_id=staff.staff_id,
            action="created_client_account",
            client_account_id=uuid.UUID(str(client_id)),
            target=name.strip(),
        )
    return RedirectResponse("/", status_code=303)


@router.get("/clients/{client_id}/people", response_class=HTMLResponse)
async def people_screen(
    request: Request,
    staff: Annotated[StaffContext, Depends(require_staff)],
    client_id: uuid.UUID,
) -> HTMLResponse:
    """FR-AUTH-4/5: client users are managed here, by Becca, on request.
    The landing page's People count links to this list."""
    async with request.app.state.db.console_session() as s:
        client = (
            await s.execute(
                text("SELECT name FROM client_account WHERE id = :cid"),
                {"cid": str(client_id)},
            )
        ).first()
        if client is None:
            return HTMLResponse("Not found", status_code=404)
        rows = (
            await s.execute(
                text(
                    "SELECT id, google_email, role, last_seen_at, created_at FROM app_user"
                    " WHERE client_account_id = :cid ORDER BY created_at"
                ),
                {"cid": str(client_id)},
            )
        ).all()
    result: HTMLResponse = request.app.state.templates.TemplateResponse(
        request,
        "console/client_people.html",
        {
            "plane": "console",
            "staff": staff,
            "client_id": client_id,
            "client_name": client[0],
            "people": [
                {"id": r[0], "email": r[1], "role": r[2], "last_seen_at": r[3], "added_at": r[4]}
                for r in rows
            ],
            "error": request.query_params.get("error"),
        },
    )
    return result


@router.post("/clients/{client_id}/people", dependencies=[Depends(check_csrf_form)])
async def add_person(
    request: Request,
    staff: Annotated[StaffContext, Depends(require_staff)],
    client_id: uuid.UUID,
    email: Annotated[str, Form()],
    role: Annotated[str, Form()] = "member",
) -> RedirectResponse:
    address = email.strip().lower()
    base = f"/clients/{client_id}/people"
    if "@" not in address:
        return RedirectResponse(f"{base}?error=email", status_code=303)
    if role not in ("owner", "member"):
        return RedirectResponse(f"{base}?error=role", status_code=303)
    async with request.app.state.db.console_session() as s:
        name = (
            await s.execute(
                text("SELECT name FROM client_account WHERE id = :cid"),
                {"cid": str(client_id)},
            )
        ).scalar_one_or_none()
        if name is None:
            return RedirectResponse("/", status_code=303)
        # google_email is globally unique: one email, one account, one
        # plane (an address that is already staff or on another client
        # cannot be added — surfaced, not swallowed).
        inserted = (
            await s.execute(
                text(
                    "INSERT INTO app_user (client_account_id, google_email, role)"
                    " VALUES (:cid, :e, :r) ON CONFLICT (google_email) DO NOTHING RETURNING id"
                ),
                {"cid": str(client_id), "e": address, "r": role},
            )
        ).scalar_one_or_none()
        if inserted is None:
            return RedirectResponse(f"{base}?error=exists", status_code=303)
        await audit.record(
            s,
            actor_type="staff",
            actor_id=staff.staff_id,
            action="added_client_user",
            client_account_id=client_id,
            target=address,
            meta={"role": role},
        )
    return RedirectResponse(base, status_code=303)


@router.post(
    "/clients/{client_id}/people/{user_id}/remove", dependencies=[Depends(check_csrf_form)]
)
async def remove_person(
    request: Request,
    staff: Annotated[StaffContext, Depends(require_staff)],
    client_id: uuid.UUID,
    user_id: uuid.UUID,
) -> RedirectResponse:
    """Removal is immediate (SD-24: identity re-derived per request), but
    never leaves the account ownerless — only an owner can launch or
    accept the acknowledgement (FR-AUTH-7, FR-CONSOLE-2A)."""
    base = f"/clients/{client_id}/people"
    async with request.app.state.db.console_session() as s:
        row = (
            await s.execute(
                text(
                    "SELECT google_email, role FROM app_user"
                    " WHERE id = :uid AND client_account_id = :cid"
                ),
                {"uid": str(user_id), "cid": str(client_id)},
            )
        ).first()
        if row is None:
            return RedirectResponse(base, status_code=303)
        if row[1] == "owner":
            owners = (
                await s.execute(
                    text(
                        "SELECT count(*) FROM app_user"
                        " WHERE client_account_id = :cid AND role = 'owner'"
                    ),
                    {"cid": str(client_id)},
                )
            ).scalar_one()
            if int(owners) <= 1:
                return RedirectResponse(f"{base}?error=last_owner", status_code=303)
        await s.execute(text("DELETE FROM app_user WHERE id = :uid"), {"uid": str(user_id)})
        await audit.record(
            s,
            actor_type="staff",
            actor_id=staff.staff_id,
            action="removed_client_user",
            client_account_id=client_id,
            target=row[0],
        )
    return RedirectResponse(base, status_code=303)


@router.post("/clients/{client_id}/enter", dependencies=[Depends(check_csrf_form)])
async def enter_client(
    request: Request,
    staff: Annotated[StaffContext, Depends(require_staff)],
    client_id: uuid.UUID,
) -> RedirectResponse:
    """FR-ARCH-2: Entering is visible (banner on every client-plane page)
    and attributed (audit row here, actor on every action there)."""
    async with request.app.state.db.console_session() as s:
        name = (
            await s.execute(
                text("SELECT name FROM client_account WHERE id = :cid"),
                {"cid": str(client_id)},
            )
        ).scalar_one_or_none()
        if name is None:
            return RedirectResponse("/", status_code=303)
        await audit.record(
            s,
            actor_type="staff",
            actor_id=staff.staff_id,
            action="entered_client_account",
            client_account_id=client_id,
            target=name,
        )
    settings = request.app.state.settings
    scheme = "http" if settings.environment == "dev" else "https"
    port = f":{request.url.port}" if request.url.port else ""
    response = RedirectResponse(f"{scheme}://{settings.client_host}{port}/", status_code=303)
    session = staff.session
    session.entered_client_id = str(client_id)
    store: SessionStore = request.app.state.sessions
    store.write(response, session)
    return response
