"""Client plane: the notification panel (FR-NOTIFY-1/3).

Read state is per reader — Becca staff Entering an account read the
same rows without consuming the client's unread badge.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from becca.services import notify
from becca.web.deps import ClientContext, check_csrf_form, require_client_context

router = APIRouter()


@router.get("/notifications", response_class=HTMLResponse)
async def panel(
    request: Request, ctx: Annotated[ClientContext, Depends(require_client_context)]
) -> HTMLResponse:
    async with request.app.state.db.client_session(ctx.client_account_id) as s:
        items = await notify.list_for(
            s, reader_id=ctx.actor_id, client_account_id=ctx.client_account_id
        )
        prefs = await notify.prefs_for(s, reader_id=ctx.actor_id, events=notify.CLIENT_EVENTS)
    result: HTMLResponse = request.app.state.templates.TemplateResponse(
        request,
        "client/notifications.html",
        {
            "plane": "client",
            "ctx": ctx,
            "nav_active": "notifications",
            "items": items,
            "prefs": prefs,
        },
    )
    return result


@router.get("/notifications/unread")
async def unread(
    request: Request, ctx: Annotated[ClientContext, Depends(require_client_context)]
) -> JSONResponse:
    async with request.app.state.db.client_session(ctx.client_account_id) as s:
        count = await notify.unread_count(
            s, reader_id=ctx.actor_id, client_account_id=ctx.client_account_id
        )
    return JSONResponse({"count": count})


@router.post("/notifications/read-all", dependencies=[Depends(check_csrf_form)])
async def read_all(
    request: Request, ctx: Annotated[ClientContext, Depends(require_client_context)]
) -> RedirectResponse:
    async with request.app.state.db.client_session(ctx.client_account_id) as s:
        await notify.mark_all_read(
            s, reader_id=ctx.actor_id, client_account_id=ctx.client_account_id
        )
    return RedirectResponse("/notifications", status_code=303)


@router.post("/notifications/prefs", dependencies=[Depends(check_csrf_form)])
async def save_prefs(
    request: Request, ctx: Annotated[ClientContext, Depends(require_client_context)]
) -> RedirectResponse:
    form = await request.form()
    async with request.app.state.db.client_session(ctx.client_account_id) as s:
        for event in notify.CLIENT_EVENTS:
            await notify.set_pref(
                s,
                reader_id=ctx.actor_id,
                client_account_id=ctx.client_account_id,
                event=event,
                enabled=event in form,
            )
    return RedirectResponse("/notifications", status_code=303)
