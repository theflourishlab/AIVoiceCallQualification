"""Console: Becca-side notifications (FR-NOTIFY-2B's staff audience).

The balance-low rows here are the record; the alert is the persistent
landing banner (FR-NOTIFY-2A).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from becca.services import notify
from becca.web.deps import StaffContext, check_csrf_form, require_staff

router = APIRouter()


@router.get("/notifications", response_class=HTMLResponse)
async def panel(
    request: Request, staff: Annotated[StaffContext, Depends(require_staff)]
) -> HTMLResponse:
    async with request.app.state.db.console_session() as s:
        items = await notify.list_for(s, reader_id=staff.staff_id, client_account_id=None)
        prefs = await notify.prefs_for(s, reader_id=staff.staff_id, events=notify.STAFF_EVENTS)
    result: HTMLResponse = request.app.state.templates.TemplateResponse(
        request,
        "console/notifications.html",
        {"plane": "console", "staff": staff, "items": items, "prefs": prefs},
    )
    return result


@router.get("/notifications/unread")
async def unread(
    request: Request, staff: Annotated[StaffContext, Depends(require_staff)]
) -> JSONResponse:
    async with request.app.state.db.console_session() as s:
        count = await notify.unread_count(s, reader_id=staff.staff_id, client_account_id=None)
    return JSONResponse({"count": count})


@router.post("/notifications/read-all", dependencies=[Depends(check_csrf_form)])
async def read_all(
    request: Request, staff: Annotated[StaffContext, Depends(require_staff)]
) -> RedirectResponse:
    async with request.app.state.db.console_session() as s:
        await notify.mark_all_read(s, reader_id=staff.staff_id, client_account_id=None)
    return RedirectResponse("/notifications", status_code=303)


@router.post("/notifications/prefs", dependencies=[Depends(check_csrf_form)])
async def save_prefs(
    request: Request, staff: Annotated[StaffContext, Depends(require_staff)]
) -> RedirectResponse:
    form = await request.form()
    async with request.app.state.db.console_session() as s:
        for event in notify.STAFF_EVENTS:
            await notify.set_pref(
                s,
                reader_id=staff.staff_id,
                client_account_id=None,
                event=event,
                enabled=event in form,
            )
    return RedirectResponse("/notifications", status_code=303)
