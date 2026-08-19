"""Results (FR-RESULT-1..8): the answers, filterable and exportable.

Columns and filters are generated from the run's frozen schema
(FR-RESULT-2) — the filter bar differs per agent and is always correct.
"Qualified" is a saved view, not a category (FR-RESULT-3). Results are
live while dialling (FR-RESULT-4), and the SSE monitor keeps the run
counters moving without a reload (SD-09: LISTEN/NOTIFY is the pub/sub).
"""

import asyncio
import csv
import io
import json
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy import text
from starlette.responses import Response

from becca.domain.views import insight_schema, variable_contract
from becca.services import agents as agents_service
from becca.services import audit
from becca.services import results as results_service
from becca.web.deps import ClientContext, check_csrf_form, require_client_context

router = APIRouter()


def _tpl(request: Request, name: str, ctx: ClientContext, **extra: object) -> HTMLResponse:
    response: HTMLResponse = request.app.state.templates.TemplateResponse(
        request, name, {"plane": "client", "ctx": ctx, "nav_active": "results", **extra}
    )
    return response


def _filter_querystring(filters: dict[int, str]) -> str:
    from urllib.parse import urlencode

    return urlencode({f"f_{k}": v for k, v in filters.items()})


@router.get("/results", response_class=HTMLResponse)
async def results_index(
    request: Request,
    ctx: Annotated[ClientContext, Depends(require_client_context)],
) -> HTMLResponse:
    async with request.app.state.db.client_session(ctx.client_account_id) as s:
        rows = (
            await s.execute(
                text(
                    """
                    SELECT a.id, a.name, a.status,
                           (SELECT count(*) FROM call c
                             WHERE c.agent_id = a.id AND c.status = 'completed'),
                           (SELECT count(DISTINCT r.call_id) FROM insight_result r
                             WHERE r.agent_id = a.id)
                      FROM agent a
                      JOIN run_schedule rs ON rs.agent_id = a.id
                     ORDER BY rs.created_at DESC
                    """
                )
            )
        ).all()
    agents = [
        {"id": r[0], "name": r[1], "status": r[2], "completed": int(r[3]), "scored": int(r[4])}
        for r in rows
    ]
    return _tpl(request, "client/results_index.html", ctx, agents=agents)


async def _load_results_context(
    request: Request, ctx: ClientContext, agent_id: uuid.UUID
) -> dict[str, Any] | None:
    async with request.app.state.db.client_session(ctx.client_account_id) as s:
        agent = await agents_service.get_agent(s, agent_id)
        if agent is None:
            return None
        outputs = list(insight_schema(agent["content"]))
        inputs = list(variable_contract(agent["content"]))
        filters = results_service.parse_filters(dict(request.query_params), outputs)
        calls = await results_service.filtered_calls(
            s,
            agent_id=agent_id,
            filters=filters,
            enum_field_ids={f.id for f in outputs if f.type == "enum"},
        )
        views = await results_service.saved_views(s, agent_id)
        counters = await results_service.run_counters(s, agent_id)
    return {
        "agent": agent,
        "outputs": outputs,
        "inputs": inputs,
        "filters": filters,
        "calls": calls,
        "views": views,
        "counters": counters,
        "querystring": _filter_querystring(filters),
    }


@router.get("/agents/{agent_id}/results", response_class=HTMLResponse)
async def results_table(
    request: Request,
    ctx: Annotated[ClientContext, Depends(require_client_context)],
    agent_id: uuid.UUID,
) -> Response:
    context = await _load_results_context(request, ctx, agent_id)
    if context is None:
        return RedirectResponse("/results", status_code=303)
    return _tpl(request, "client/agent_results.html", ctx, **context)


@router.post("/agents/{agent_id}/views", dependencies=[Depends(check_csrf_form)])
async def create_saved_view(
    request: Request,
    ctx: Annotated[ClientContext, Depends(require_client_context)],
    agent_id: uuid.UUID,
    name: Annotated[str, Form()],
) -> Response:
    """The current filter combination, named (FR-RESULT-3)."""
    form = {k: str(v) for k, v in (await request.form()).items() if isinstance(v, str)}
    async with request.app.state.db.client_session(ctx.client_account_id) as s:
        agent = await agents_service.get_agent(s, agent_id)
        if agent is None or not name.strip():
            return RedirectResponse(f"/agents/{agent_id}/results", status_code=303)
        outputs = list(insight_schema(agent["content"]))
        filters = results_service.parse_filters(form, outputs)
        await results_service.save_view(
            s,
            agent_id=agent_id,
            client_account_id=ctx.client_account_id,
            name=name,
            filters=filters,
        )
    return RedirectResponse(
        f"/agents/{agent_id}/results?{_filter_querystring(filters)}", status_code=303
    )


@router.get("/agents/{agent_id}/results.csv")
async def export_csv(
    request: Request,
    ctx: Annotated[ClientContext, Depends(require_client_context)],
    agent_id: uuid.UUID,
) -> Response:
    """The current filtered view, exactly as seen (FR-RESULT-5):
    contact fields and schema fields as columns, corrections shown."""
    context = await _load_results_context(request, ctx, agent_id)
    if context is None:
        return RedirectResponse("/results", status_code=303)
    inputs, outputs, calls = context["inputs"], context["outputs"], context["calls"]

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["phone", *(f.key for f in inputs), *(f.key for f in outputs), "duration_sec", "called_at"]
    )
    for call in calls:
        writer.writerow(
            [
                call["phone"] or "",
                *(call["contact_variables"].get(str(f.id), "") for f in inputs),
                *((call["results"].get(f.id) or {}).get("shown", "") or "" for f in outputs),
                call["duration_sec"] or "",
                call["started_at"].isoformat() if call["started_at"] else "",
            ]
        )
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{context["agent"]["name"]}-results.csv"'
        },
    )


@router.get("/agents/{agent_id}/calls/{call_id}", response_class=HTMLResponse)
async def call_detail(
    request: Request,
    ctx: Annotated[ClientContext, Depends(require_client_context)],
    agent_id: uuid.UUID,
    call_id: uuid.UUID,
) -> Response:
    context = await _load_results_context(request, ctx, agent_id)
    if context is None:
        return RedirectResponse("/results", status_code=303)
    calls = context["calls"]
    current = next((c for c in calls if c["id"] == call_id), None)
    if current is None:
        return RedirectResponse(f"/agents/{agent_id}/results", status_code=303)

    # Prev/next scoped to the filter set the user arrived with
    # (FR-RESULT-8) — the querystring travels with the links.
    index = calls.index(current)
    prev_id = calls[index - 1]["id"] if index > 0 else None
    next_id = calls[index + 1]["id"] if index + 1 < len(calls) else None

    async with request.app.state.db.client_session(ctx.client_account_id) as s:
        messages = await results_service.transcript_messages(s, call_id)
    return _tpl(
        request,
        "client/call_detail.html",
        ctx,
        **context,
        call=current,
        messages=messages or [],
        position=index + 1,
        total=len(calls),
        prev_id=prev_id,
        next_id=next_id,
    )


@router.post("/agents/{agent_id}/calls/{call_id}/correct", dependencies=[Depends(check_csrf_form)])
async def correct_value(
    request: Request,
    ctx: Annotated[ClientContext, Depends(require_client_context)],
    agent_id: uuid.UUID,
    call_id: uuid.UUID,
    field_id: Annotated[int, Form()],
    corrected_value: Annotated[str, Form()] = "",
    return_qs: Annotated[str, Form()] = "",
) -> Response:
    async with request.app.state.db.client_session(ctx.client_account_id) as s:
        await results_service.correct_value(
            s,
            call_id=call_id,
            field_id=field_id,
            corrected_value=corrected_value,
            corrected_by=ctx.actor_id,
        )
        await audit.record(
            s,
            actor_type=ctx.actor_type,
            actor_id=ctx.actor_id,
            action="corrected_result",
            client_account_id=ctx.client_account_id,
            target=str(call_id),
            meta={"field_id": field_id},
        )
    suffix = f"?{return_qs}" if return_qs else ""
    return RedirectResponse(f"/agents/{agent_id}/calls/{call_id}{suffix}", status_code=303)


@router.get("/agents/{agent_id}/calls/{call_id}/recording")
async def recording_playback(
    request: Request,
    ctx: Annotated[ClientContext, Depends(require_client_context)],
    agent_id: uuid.UUID,
    call_id: uuid.UUID,
) -> Response:
    """FR-REC-3/4: we store IDs only; a playback URL is minted fresh on
    every request and never persisted."""
    async with request.app.state.db.client_session(ctx.client_account_id) as s:
        recording_id = (
            await s.execute(
                text("SELECT telnyx_recording_id FROM call WHERE id = :id AND agent_id = :aid"),
                {"id": str(call_id), "aid": str(agent_id)},
            )
        ).scalar_one_or_none()
    if not recording_id:
        return RedirectResponse(f"/agents/{agent_id}/calls/{call_id}", status_code=303)
    url = await request.app.state.telnyx.get_recording_url(recording_id=recording_id)
    return RedirectResponse(url, status_code=302)


@router.get("/agents/{agent_id}/run-events")
async def run_events(
    request: Request,
    ctx: Annotated[ClientContext, Depends(require_client_context)],
    agent_id: uuid.UUID,
) -> StreamingResponse:
    """SSE: run counters, pushed when the worker or ingest NOTIFYs, with
    a heartbeat requery so a missed notification only delays, never
    strands. The stream ends itself once the run stops moving."""
    db = request.app.state.db

    async def stream() -> Any:
        queue: asyncio.Queue[str] = asyncio.Queue()

        def on_notify(_conn: Any, _pid: Any, _channel: Any, payload: str) -> None:
            queue.put_nowait(payload)

        engine = db.engine
        async with engine.connect() as conn:
            raw = await conn.get_raw_connection()
            await raw.driver_connection.add_listener("run_progress", on_notify)
            try:
                while True:
                    async with db.client_session(ctx.client_account_id) as s:
                        counters = await results_service.run_counters(s, agent_id)
                    yield f"data: {json.dumps(counters)}\n\n"
                    if counters["status"] not in ("scheduled", "dialling"):
                        break  # finished/halted: one last snapshot, then close
                    try:
                        payload = await asyncio.wait_for(queue.get(), timeout=5.0)
                        if payload and payload != str(agent_id):
                            continue  # someone else's run; requery cheaply anyway
                    except TimeoutError:
                        pass  # heartbeat requery
                    if await request.is_disconnected():
                        break
            finally:
                await raw.driver_connection.remove_listener("run_progress", on_notify)

    return StreamingResponse(stream(), media_type="text/event-stream")
