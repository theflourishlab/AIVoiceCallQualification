"""The voice & behaviour screen (prototype screen 05, FR-AGENT-9/10/11).

Wizard step 3. Choices are stored as overrides on the agent row and
resolved against deployment defaults at every assistant sync — so an
agent that never visits this screen behaves exactly as before it
existed. Frozen agents (FR-LAUNCH-7 statuses) see their settings
read-only, same rule as the test screen.

The preview proxies Telnyx TTS through our backend (FR-AGENT-11): the
browser gets audio bytes, never the API key. Preview speed clamps to
0.5-2.0, narrower than the assistant's own 0.25-2.0.
"""

import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import text

from becca.services import agents as agents_service
from becca.services import audit
from becca.services import voice_config as vc
from becca.telnyx.gateway import TelnyxError
from becca.web.deps import ClientContext, check_csrf_form, require_client_context

router = APIRouter()


@router.get("/agents/{agent_id}/voice", response_class=HTMLResponse)
async def voice_screen(
    request: Request,
    ctx: Annotated[ClientContext, Depends(require_client_context)],
    agent_id: uuid.UUID,
) -> Response:
    settings = request.app.state.settings
    async with request.app.state.db.client_session(ctx.client_account_id) as s:
        agent = await agents_service.get_agent(s, agent_id)
    if agent is None:
        return RedirectResponse("/", status_code=303)
    effective = vc.resolve(agent["voice_config"], settings)
    result: HTMLResponse = request.app.state.templates.TemplateResponse(
        request,
        "client/agent_voice.html",
        {
            "plane": "client",
            "ctx": ctx,
            "agent": agent,
            "effective": effective,
            "voices": vc.VOICE_CATALOG,
            "models": vc.CONVERSATION_MODELS,
            "stt_models": vc.TRANSCRIPTION_MODELS,
            "frozen": agent["status"] not in ("draft", "tested"),
            "saved": request.query_params.get("saved") == "1",
        },
    )
    return result


@router.post("/agents/{agent_id}/voice", dependencies=[Depends(check_csrf_form)])
async def save_voice_config(
    request: Request,
    ctx: Annotated[ClientContext, Depends(require_client_context)],
    agent_id: uuid.UUID,
) -> Response:
    form = {k: str(v) for k, v in (await request.form()).items() if isinstance(v, str)}
    async with request.app.state.db.client_session(ctx.client_account_id) as s:
        agent = await agents_service.get_agent(s, agent_id)
        if agent is None:
            return RedirectResponse("/", status_code=303)
        # Same freeze rule as the schema (FR-LAUNCH-7): a launched
        # agent's run assistant is never mutated, so accepting edits
        # here would be a silent lie.
        if agent["status"] not in ("draft", "tested"):
            return RedirectResponse(f"/agents/{agent_id}/voice", status_code=303)
        overrides = vc.overrides_from_form(form)
        await s.execute(
            text("UPDATE agent SET voice_config = :cfg WHERE id = :aid"),
            {"cfg": json.dumps(overrides), "aid": str(agent_id)},
        )
        await audit.record(
            s,
            actor_type=ctx.actor_type,
            actor_id=ctx.actor_id,
            action="saved_voice_config",
            client_account_id=ctx.client_account_id,
            target=str(agent_id),
            meta=overrides,
        )
    return RedirectResponse(f"/agents/{agent_id}/voice?saved=1", status_code=303)


@router.get("/agents/{agent_id}/voice/preview")
async def voice_preview(
    request: Request,
    ctx: Annotated[ClientContext, Depends(require_client_context)],
    agent_id: uuid.UUID,
    voice: str,
    speed: str = "1.0",
) -> Response:
    """FR-AGENT-11. Only catalog voices are previewable — the endpoint
    is not an open TTS proxy."""
    if voice not in {v.id for v in vc.VOICE_CATALOG}:
        return Response(status_code=404)
    gateway = request.app.state.telnyx
    try:
        audio = await gateway.synthesize_speech(
            voice=voice, text=vc.PREVIEW_TEXT, voice_speed=vc.clamp_preview_speed(speed)
        )
    except TelnyxError:
        return Response(status_code=502)
    return Response(
        content=audio,
        media_type="audio/mpeg",
        headers={"Cache-Control": "private, max-age=3600"},
    )
