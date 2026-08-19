"""Schedule & launch (FR-LAUNCH-1/2), and the run controls
(FR-DISPATCH-6/7). Launching is the one permission separating owner
from member (FR-AUTH-7); pause/resume/stop are member actions.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text
from starlette.responses import Response

from becca.services import agents as agents_service
from becca.services import audit
from becca.services import contacts as contacts_service
from becca.services import launch as launch_service
from becca.services import voice_config as voice_config_service
from becca.web.deps import (
    ClientContext,
    check_csrf_form,
    require_client_context,
    require_owner,
)

router = APIRouter()


def _parse_bands(raw: str) -> list[dict[str, str]]:
    """ "13:00-14:00, 16:30-17:15" -> excluded band dicts. Bad chunks are
    dropped rather than blocking a launch over a typo in an optional
    field."""
    bands = []
    for chunk in raw.split(","):
        parts = chunk.strip().split("-")
        if len(parts) == 2:
            try:
                from datetime import time

                start, end = (time.fromisoformat(p.strip()) for p in parts)
            except ValueError:
                continue
            bands.append({"start": start.isoformat("minutes"), "end": end.isoformat("minutes")})
    return bands


async def _queue_stats(s: Any, agent_id: uuid.UUID) -> dict[str, int]:
    rows = await s.execute(
        text("SELECT state, count(*) FROM queue_item WHERE agent_id = :aid GROUP BY state"),
        {"aid": str(agent_id)},
    )
    stats = {state: int(n) for state, n in rows}
    stats["total"] = sum(stats.values())
    return stats


@router.get("/agents/{agent_id}/launch", response_class=HTMLResponse)
async def launch_screen(
    request: Request,
    ctx: Annotated[ClientContext, Depends(require_client_context)],
    agent_id: uuid.UUID,
) -> Response:
    settings = request.app.state.settings
    async with request.app.state.db.client_session(ctx.client_account_id) as s:
        agent = await agents_service.get_agent(s, agent_id)
        if agent is None:
            return RedirectResponse("/", status_code=303)

        lists = [
            e for e in await contacts_service.list_lists(s) if str(e["agent_id"]) == str(agent_id)
        ]
        wanted = request.query_params.get("list", "")
        list_entry = next((e for e in lists if str(e["id"]) == wanted), None) or (
            lists[0] if lists else None
        )

        running = agent["status"] in ("scheduled", "dialling", "finished", "halted")
        schedule = paused = None
        stats: dict[str, int] = {}
        if running:
            schedule = (
                await s.execute(
                    text(
                        "SELECT window_start, window_end, days, timezone, spend_cap,"
                        " paused, retry_attempts FROM run_schedule WHERE agent_id = :aid"
                    ),
                    {"aid": str(agent_id)},
                )
            ).first()
            paused = bool(schedule[5]) if schedule else None
            stats = await _queue_stats(s, agent_id)

        result = await launch_service.preflight(
            s,
            agent=agent,
            list_entry=list_entry,
            client_account_id=ctx.client_account_id,
            telnyx_mode=settings.telnyx_mode,
            from_number=settings.telnyx_from_number,
            max_call_minutes=settings.max_call_minutes,
            estimated_call_minutes=settings.estimated_call_minutes,
        )
        acknowledged = next(c.ok for c in result.blocking if c.key == "acknowledged")
        rate_per_min = (
            await s.execute(
                text("SELECT rate_per_min_usd FROM client_account WHERE id = :cid"),
                {"cid": str(ctx.client_account_id)},
            )
        ).scalar_one()

    diallable = int(list_entry["diallable_count"]) if list_entry else 0
    # Suggested cap: the whole list at the client's rate and a typical
    # duration, x1.5 buffer (the quote-high habit survives the wallet).
    suggested_cap = max(
        5.0,
        round(diallable * float(rate_per_min) * settings.estimated_call_minutes * 1.5, 0),
    )
    response: HTMLResponse = request.app.state.templates.TemplateResponse(
        request,
        "client/agent_launch.html",
        {
            "plane": "client",
            "ctx": ctx,
            "nav_active": "agents",
            "agent": agent,
            "lists": lists,
            "list_entry": list_entry,
            "preflight": result,
            "acknowledged": acknowledged,
            "running": running,
            "schedule": schedule,
            "paused": paused,
            "stats": stats,
            "diallable": diallable,
            "suggested_cap": suggested_cap,
            "error": request.query_params.get("error"),
        },
    )
    return response


@router.post("/agents/{agent_id}/acknowledge", dependencies=[Depends(check_csrf_form)])
async def accept_acknowledgement(
    request: Request,
    ctx: Annotated[ClientContext, Depends(require_owner)],
    agent_id: uuid.UUID,
) -> Response:
    """FR-CONSOLE-2A: the owner's one-time statement that their lists
    expect contact and that they hold the required permission."""
    async with request.app.state.db.client_session(ctx.client_account_id) as s:
        await launch_service.acknowledge(
            s, client_account_id=ctx.client_account_id, user_id=ctx.actor_id
        )
        await audit.record(
            s,
            actor_type=ctx.actor_type,
            actor_id=ctx.actor_id,
            action="accepted_acknowledgement",
            client_account_id=ctx.client_account_id,
        )
    return RedirectResponse(f"/agents/{agent_id}/launch", status_code=303)


@router.post("/agents/{agent_id}/launch", dependencies=[Depends(check_csrf_form)])
async def launch_agent(
    request: Request,
    ctx: Annotated[ClientContext, Depends(require_owner)],
    agent_id: uuid.UUID,
    list_id: Annotated[uuid.UUID, Form()],
    window_start: Annotated[str, Form()],
    window_end: Annotated[str, Form()],
    spend_cap: Annotated[float, Form()],
    retry_attempts: Annotated[int, Form()] = 0,
    retry_interval_secs: Annotated[int, Form()] = 3600,
    excluded_bands: Annotated[str, Form()] = "",
    not_before: Annotated[str, Form()] = "",
) -> Response:
    settings = request.app.state.settings
    form = await request.form()
    days = [d for d in range(1, 8) if form.get(f"day_{d}")]
    if not days:
        return RedirectResponse(f"/agents/{agent_id}/launch?error=days", status_code=303)

    async with request.app.state.db.client_session(ctx.client_account_id) as s:
        agent = await agents_service.get_agent(s, agent_id)
        list_entry = await contacts_service.get_list(s, list_id)
        if agent is None or list_entry is None:
            return RedirectResponse("/", status_code=303)
        if str(list_entry["agent_id"]) != str(agent_id):
            # The upload-side FK makes this unreachable from our UI; a
            # forged form still must not dial agent A with agent B's list.
            return RedirectResponse(f"/agents/{agent_id}/launch?error=list", status_code=303)
        try:
            await launch_service.launch(
                s,
                request.app.state.telnyx,
                agent=agent,
                list_entry=list_entry,
                client_account_id=ctx.client_account_id,
                telnyx_mode=settings.telnyx_mode,
                from_number=settings.telnyx_from_number,
                voice_config=voice_config_service.resolve(agent["voice_config"], settings),
                window_start=window_start,
                window_end=window_end,
                days=days,
                excluded_bands=_parse_bands(excluded_bands),
                timezone="Africa/Lagos",  # one timezone per account (SD-21)
                retry_attempts=max(0, retry_attempts),
                retry_interval_secs=max(60, retry_interval_secs),
                spend_cap=spend_cap,
                not_before=not_before.strip() or None,
            )
        except launch_service.LaunchRefused:
            return RedirectResponse(f"/agents/{agent_id}/launch?error=preflight", status_code=303)
        await audit.record(
            s,
            actor_type=ctx.actor_type,
            actor_id=ctx.actor_id,
            action="launched_agent",
            client_account_id=ctx.client_account_id,
            target=str(agent_id),
            meta={"list_id": str(list_id), "spend_cap": spend_cap},
        )
    return RedirectResponse(f"/agents/{agent_id}/launch", status_code=303)


async def _set_paused(
    request: Request, ctx: ClientContext, agent_id: uuid.UUID, paused: bool, action: str
) -> Response:
    async with request.app.state.db.client_session(ctx.client_account_id) as s:
        await s.execute(
            text("UPDATE run_schedule SET paused = :p WHERE agent_id = :aid"),
            {"p": paused, "aid": str(agent_id)},
        )
        await audit.record(
            s,
            actor_type=ctx.actor_type,
            actor_id=ctx.actor_id,
            action=action,
            client_account_id=ctx.client_account_id,
            target=str(agent_id),
        )
    return RedirectResponse(f"/agents/{agent_id}/launch", status_code=303)


@router.post("/agents/{agent_id}/pause", dependencies=[Depends(check_csrf_form)])
async def pause_run(
    request: Request,
    ctx: Annotated[ClientContext, Depends(require_client_context)],
    agent_id: uuid.UUID,
) -> Response:
    """FR-DISPATCH-6: a flag the worker reads. Nothing goes to Telnyx;
    calls in flight complete."""
    return await _set_paused(request, ctx, agent_id, True, "paused_run")


@router.post("/agents/{agent_id}/resume", dependencies=[Depends(check_csrf_form)])
async def resume_run(
    request: Request,
    ctx: Annotated[ClientContext, Depends(require_client_context)],
    agent_id: uuid.UUID,
) -> Response:
    return await _set_paused(request, ctx, agent_id, False, "resumed_run")


@router.post("/agents/{agent_id}/stop", dependencies=[Depends(check_csrf_form)])
async def stop_run(
    request: Request,
    ctx: Annotated[ClientContext, Depends(require_client_context)],
    agent_id: uuid.UUID,
) -> Response:
    """FR-DISPATCH-7: drain the queue, mark finished. Undialled contacts
    stay undialled; the run assistant is retained so results survive."""
    async with request.app.state.db.client_session(ctx.client_account_id) as s:
        await s.execute(
            text(
                "UPDATE queue_item SET state = 'skipped'"
                " WHERE agent_id = :aid AND state = 'pending'"
            ),
            {"aid": str(agent_id)},
        )
        await s.execute(
            text(
                "UPDATE agent SET status = 'finished'"
                " WHERE id = :aid AND status IN ('scheduled', 'dialling')"
            ),
            {"aid": str(agent_id)},
        )
        await audit.record(
            s,
            actor_type=ctx.actor_type,
            actor_id=ctx.actor_id,
            action="stopped_run",
            client_account_id=ctx.client_account_id,
            target=str(agent_id),
        )
    return RedirectResponse(f"/agents/{agent_id}/launch", status_code=303)
