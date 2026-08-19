"""Client plane: the Overview (prototype screen 01; hybrid verdict
14 Aug 2026 — variant A's top half, variant B's agents table; primary
source on branch prototype/overview).

First thing worth checking each morning: is anything running, did
anything break, what did today produce and cost. Auto-refreshes every
4 seconds only while something is actually dialling (the test screen's
meta-refresh precedent) — a quiet Overview is fully static.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

from becca.services import overview as overview_service
from becca.web.deps import ClientContext, require_client_context

router = APIRouter()


@router.get("/overview", response_class=HTMLResponse)
async def overview_screen(
    request: Request, ctx: Annotated[ClientContext, Depends(require_client_context)]
) -> HTMLResponse:
    async with request.app.state.db.client_session(ctx.client_account_id) as s:
        live = await overview_service.live_state(s, client_account_id=ctx.client_account_id)
        kpi = await overview_service.today_kpis(s, client_account_id=ctx.client_account_id)
        hours = await overview_service.calls_by_hour(s, client_account_id=ctx.client_account_id)
        outcome = await overview_service.outcomes(s, client_account_id=ctx.client_account_id)
        agents = await overview_service.agents_table(s, client_account_id=ctx.client_account_id)
        rate = (
            await s.execute(
                text("SELECT rate_per_min_usd FROM client_account WHERE id = :cid"),
                {"cid": str(ctx.client_account_id)},
            )
        ).scalar_one()
    result: HTMLResponse = request.app.state.templates.TemplateResponse(
        request,
        "client/overview.html",
        {
            "plane": "client",
            "ctx": ctx,
            "nav_active": "overview",
            "live": live,
            "kpi": kpi,
            "hours": hours,
            "max_hour": max(hours) if any(hours) else 0,
            "outcome": outcome,
            "outcome_total": sum(outcome.values()),
            "agents": agents,
            "rate": float(rate),
            "refreshing": live["in_flight"] > 0 or live["dialling_agents"] > 0,
        },
    )
    return result
