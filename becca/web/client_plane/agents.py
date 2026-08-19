"""Client plane: agents list, the brief, and the review screen.

Phase 1 scope: an agent can be created and reviewed (the gate). The rest
of the wizard (voice, test, contacts, launch) arrives in Phases 2-4.
"""

import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.responses import Response

from becca.domain.codec import content_from_json, content_to_json
from becca.domain.model import DanglingFieldRef, DuplicateFieldKey
from becca.domain.serialize import render_instructions
from becca.domain.views import insight_schema, variable_contract
from becca.generation.generate import GenerationFailed
from becca.services import agents as agents_service
from becca.services import audit
from becca.web.deps import ClientContext, check_csrf_form, require_client_context

router = APIRouter()


def _tpl(request: Request, name: str, ctx: ClientContext, **extra: object) -> HTMLResponse:
    result: HTMLResponse = request.app.state.templates.TemplateResponse(
        request, name, {"plane": "client", "ctx": ctx, **extra}
    )
    return result


@router.get("/", response_class=HTMLResponse)
async def agents_list(
    request: Request, ctx: Annotated[ClientContext, Depends(require_client_context)]
) -> HTMLResponse:
    async with request.app.state.db.client_session(ctx.client_account_id) as s:
        agent_rows = await agents_service.list_agents(s)
    return _tpl(request, "client/agents_list.html", ctx, agents=agent_rows)


@router.get("/agents/new", response_class=HTMLResponse)
async def new_agent_form(
    request: Request, ctx: Annotated[ClientContext, Depends(require_client_context)]
) -> HTMLResponse:
    return _tpl(request, "client/agent_new.html", ctx, error=None)


@router.post("/agents", dependencies=[Depends(check_csrf_form)])
async def create_agent(
    request: Request,
    ctx: Annotated[ClientContext, Depends(require_client_context)],
    brief: Annotated[str, Form()],
) -> Response:
    generator = request.app.state.generator
    try:
        generated = await generator.generate(brief)
    except GenerationFailed:
        return _tpl(
            request,
            "client/agent_new.html",
            ctx,
            error="Becca could not build that agent. Try describing the call differently.",
        )
    async with request.app.state.db.client_session(ctx.client_account_id) as s:
        agent_id = await agents_service.create_agent(
            s,
            client_account_id=ctx.client_account_id,
            name=generated.name,
            content=generated.content,
            brief=brief.strip(),
        )
        await audit.record(
            s,
            actor_type=ctx.actor_type,
            actor_id=ctx.actor_id,
            action="created_agent",
            client_account_id=ctx.client_account_id,
            target=str(agent_id),
        )
    return RedirectResponse(f"/agents/{agent_id}", status_code=303)


@router.get("/agents/{agent_id}", response_class=HTMLResponse)
async def review_agent(
    request: Request,
    ctx: Annotated[ClientContext, Depends(require_client_context)],
    agent_id: uuid.UUID,
) -> Response:
    async with request.app.state.db.client_session(ctx.client_account_id) as s:
        agent = await agents_service.get_agent(s, agent_id)
    if agent is None:
        return RedirectResponse("/", status_code=303)
    content = agent["content"]
    from becca.domain.model import FieldRef

    uses: dict[int, int] = {}
    for block in content.script_blocks:
        if isinstance(block, FieldRef):
            uses[block.field_id] = uses.get(block.field_id, 0) + 1
    return _tpl(
        request,
        "client/agent_review.html",
        ctx,
        agent=agent,
        content=content,
        inputs=variable_contract(content),
        outputs=insight_schema(content),
        input_uses=uses,
        spoken_preview=render_instructions(content),
        field_data=content_to_json(content)["fields"],
        error=None,
    )


@router.post("/agents/{agent_id}/versions", dependencies=[Depends(check_csrf_form)])
async def save_version(
    request: Request,
    ctx: Annotated[ClientContext, Depends(require_client_context)],
    agent_id: uuid.UUID,
    content_json: Annotated[str, Form()],
    return_to: Annotated[str, Form()] = "",
) -> Response:
    """FR-AGENT-8: editing produces a new version. The submitted JSON is
    decoded through the domain constructor — the FR-AGENT-3A boundary —
    though the chip editor cannot express a dangling ref to begin with."""
    back = f"/agents/{agent_id}/test" if return_to == "test" else f"/agents/{agent_id}"
    try:
        content = content_from_json(json.loads(content_json))
    except DanglingFieldRef, DuplicateFieldKey, ValueError, KeyError:
        return RedirectResponse(back, status_code=303)
    async with request.app.state.db.client_session(ctx.client_account_id) as s:
        agent = await agents_service.get_agent(s, agent_id)
        if agent is None:
            return RedirectResponse("/", status_code=303)
        try:
            n = await agents_service.save_new_version(
                s, agent_id=agent_id, client_account_id=ctx.client_account_id, content=content
            )
        except agents_service.AgentFrozen:
            # FR-LAUNCH-7: launched agents are read-only; duplicate to change.
            return RedirectResponse(back, status_code=303)
        await audit.record(
            s,
            actor_type=ctx.actor_type,
            actor_id=ctx.actor_id,
            action="saved_agent_version",
            client_account_id=ctx.client_account_id,
            target=str(agent_id),
            meta={"version": n},
        )
    return RedirectResponse(back, status_code=303)


@router.get("/agents/{agent_id}/describe", response_class=HTMLResponse)
async def describe_screen(
    request: Request,
    ctx: Annotated[ClientContext, Depends(require_client_context)],
    agent_id: uuid.UUID,
) -> Response:
    """Step 1, reopenable pre-launch (user feedback, 14 Aug 2026): the
    stored brief, editable, with rebuild-from-description."""
    async with request.app.state.db.client_session(ctx.client_account_id) as s:
        agent = await agents_service.get_agent(s, agent_id)
    if agent is None:
        return RedirectResponse("/", status_code=303)
    return _tpl(
        request,
        "client/agent_describe.html",
        ctx,
        agent=agent,
        frozen=agent["status"] not in ("draft", "tested"),
        error=request.query_params.get("error"),
    )


@router.post("/agents/{agent_id}/describe", dependencies=[Depends(check_csrf_form)])
async def redescribe_agent(
    request: Request,
    ctx: Annotated[ClientContext, Depends(require_client_context)],
    agent_id: uuid.UUID,
    brief: Annotated[str, Form()],
) -> Response:
    async with request.app.state.db.client_session(ctx.client_account_id) as s:
        agent = await agents_service.get_agent(s, agent_id)
    if agent is None:
        return RedirectResponse("/", status_code=303)
    # Status gate BEFORE the generator runs — same rule as regenerate:
    # a frozen agent must not burn a generation call.
    if agent["status"] not in ("draft", "tested"):
        return RedirectResponse(f"/agents/{agent_id}/describe", status_code=303)
    generator = request.app.state.generator
    try:
        generated = await generator.generate(brief)
    except GenerationFailed:
        return RedirectResponse(f"/agents/{agent_id}/describe?error=generation", status_code=303)
    from sqlalchemy import text

    async with request.app.state.db.client_session(ctx.client_account_id) as s:
        # The user's chosen name survives a rebuild — only the content
        # and the stored brief change.
        n = await agents_service.save_new_version(
            s, agent_id=agent_id, client_account_id=ctx.client_account_id, content=generated.content
        )
        await s.execute(
            text("UPDATE agent SET brief = :b WHERE id = :aid"),
            {"b": brief.strip(), "aid": str(agent_id)},
        )
        await audit.record(
            s,
            actor_type=ctx.actor_type,
            actor_id=ctx.actor_id,
            action="redescribed_agent",
            client_account_id=ctx.client_account_id,
            target=str(agent_id),
            meta={"version": n},
        )
    return RedirectResponse(f"/agents/{agent_id}", status_code=303)


@router.post("/agents/{agent_id}/delete", dependencies=[Depends(check_csrf_form)])
async def delete_agent(
    request: Request,
    ctx: Annotated[ClientContext, Depends(require_client_context)],
    agent_id: uuid.UUID,
) -> Response:
    async with request.app.state.db.client_session(ctx.client_account_id) as s:
        agent = await agents_service.get_agent(s, agent_id)
        if agent is None:
            return RedirectResponse("/", status_code=303)
        try:
            scratch = await agents_service.delete_agent(s, agent_id)
        except agents_service.AgentFrozen:
            # Launched agents keep their records (FR-LAUNCH-7); the
            # review screen explains instead of offering the button.
            return RedirectResponse(f"/agents/{agent_id}", status_code=303)
        await audit.record(
            s,
            actor_type=ctx.actor_type,
            actor_id=ctx.actor_id,
            action="deleted_agent",
            client_account_id=ctx.client_account_id,
            target=str(agent_id),
            # The billed test calls' session ids ride along so the §11a
            # reconciliation can still explain their Telnyx dollars.
            meta={"name": agent["name"], "test_calls": scratch["test_calls"]},
        )
    # Provider cleanup AFTER the commit, best-effort: the agent row that
    # would let the sweeper find these ids is gone, so try now; a failure
    # leaks at most one idle scratch assistant, never blocks the delete.
    import contextlib

    gateway = request.app.state.telnyx
    if scratch.get("scratch_assistant_id"):
        with contextlib.suppress(Exception):
            await gateway.delete_assistant(assistant_id=str(scratch["scratch_assistant_id"]))
    if scratch.get("scratch_texml_app_id"):
        with contextlib.suppress(Exception):
            await gateway.delete_texml_application(
                texml_app_id=str(scratch["scratch_texml_app_id"])
            )
    return RedirectResponse("/", status_code=303)


@router.post("/agents/{agent_id}/name", dependencies=[Depends(check_csrf_form)])
async def rename_agent(
    request: Request,
    ctx: Annotated[ClientContext, Depends(require_client_context)],
    agent_id: uuid.UUID,
    name: Annotated[str, Form()],
) -> Response:
    """The agent's universal display name (user feedback, 14 Aug 2026).
    A label, not content — renaming is allowed in any status and never
    creates a version; FR-LAUNCH-7 freezes the schema, not the name."""
    from sqlalchemy import text

    clean = " ".join(name.split())[:80]
    async with request.app.state.db.client_session(ctx.client_account_id) as s:
        agent = await agents_service.get_agent(s, agent_id)
        if agent is None:
            return RedirectResponse("/", status_code=303)
        if clean and clean != agent["name"]:
            await s.execute(
                text("UPDATE agent SET name = :n WHERE id = :aid"),
                {"n": clean, "aid": str(agent_id)},
            )
            await audit.record(
                s,
                actor_type=ctx.actor_type,
                actor_id=ctx.actor_id,
                action="renamed_agent",
                client_account_id=ctx.client_account_id,
                target=str(agent_id),
                meta={"from": agent["name"], "to": clean},
            )
    return RedirectResponse(f"/agents/{agent_id}", status_code=303)


@router.post("/leave")
async def leave_client_account(
    request: Request, ctx: Annotated[ClientContext, Depends(require_client_context)]
) -> Response:
    """Staff leaving an Entered account (FR-ARCH-2 banner action)."""
    from becca.web.sessions import SessionStore

    if not ctx.entered:
        return RedirectResponse("/", status_code=303)
    settings = request.app.state.settings
    scheme = "http" if settings.environment == "dev" else "https"
    port = f":{request.url.port}" if request.url.port else ""
    response = RedirectResponse(f"{scheme}://{settings.console_host}{port}/", status_code=303)
    session = ctx.session
    session.entered_client_id = None
    store: SessionStore = request.app.state.sessions
    store.write(response, session)
    return response
