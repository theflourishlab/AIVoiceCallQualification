"""Contacts: agent-first import and column mapping (FRD §6).

The agent comes first because it decides which columns the file must
contain (FR-CONTACT-1). Every mapping change recomputes the stored rows
from the original upload.
"""

import json
import uuid
from dataclasses import replace
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from becca.domain.codec import content_from_json
from becca.domain.model import AgentVersionContent, Field
from becca.domain.views import variable_contract
from becca.services import agents as agents_service
from becca.services import audit, notify
from becca.services import contacts as contacts_service
from becca.web.deps import ClientContext, check_csrf_form, require_client_context

router = APIRouter()


def _tpl(request: Request, name: str, ctx: ClientContext, **extra: object) -> HTMLResponse:
    result: HTMLResponse = request.app.state.templates.TemplateResponse(
        request, name, {"plane": "client", "ctx": ctx, "nav_active": "contacts", **extra}
    )
    return result


async def _agent_cards(session: AsyncSession) -> list[dict[str, Any]]:
    """Agents with the columns each will demand and their tested count —
    what decides whether a spreadsheet will work (prototype screen 07)."""
    rows = await session.execute(
        text(
            "SELECT a.id, a.name, a.status, coalesce(v.n, 0), v.fields, v.script_blocks,"
            " (SELECT count(*) FROM test_run t WHERE t.agent_id = a.id) AS tests"
            " FROM agent a LEFT JOIN agent_version v ON v.id = a.current_version_id"
            " ORDER BY a.created_at DESC"
        )
    )
    cards: list[dict[str, Any]] = []
    for r in rows:
        contract: tuple[Field, ...] = ()
        if r[4] is not None:
            fields = r[4] if isinstance(r[4], list) else json.loads(r[4])
            blocks = r[5] if isinstance(r[5], list) else json.loads(r[5])
            content = content_from_json({"fields": fields, "script_blocks": blocks})
            contract = variable_contract(content)
        cards.append(
            {
                "id": r[0],
                "name": r[1],
                "status": r[2],
                "version_n": r[3],
                "contract": contract,
                "tests": int(r[6]),
            }
        )
    return cards


def _content_of(agent: dict[str, Any]) -> AgentVersionContent:
    content: AgentVersionContent = agent["content"]
    return content


def _mapped_ok(contract: tuple[Field, ...], mapping: contacts_service.Mapping) -> bool:
    return (
        not contacts_service.unmapped_required(contract, mapping)
        and contacts_service.phone_column(mapping) is not None
    )


@router.get("/contacts", response_class=HTMLResponse)
async def pick_agent(
    request: Request,
    ctx: Annotated[ClientContext, Depends(require_client_context)],
) -> HTMLResponse:
    selected = request.query_params.get("agent", "")
    async with request.app.state.db.client_session(ctx.client_account_id) as s:
        cards = await _agent_cards(s)
        lists = await contacts_service.list_lists(s)
    by_id = {str(c["id"]): c for c in cards}
    for entry in lists:
        card = by_id.get(str(entry["agent_id"]))
        entry["mapped"] = card is not None and _mapped_ok(card["contract"], entry["column_mapping"])
    # The redesigned picker (prototyped 14 Aug 2026): each agent row
    # carries its real contact total; a list surfaces separately only
    # while its mapping is unfinished.
    for card in cards:
        mapped = [e for e in lists if str(e["agent_id"]) == str(card["id"]) and e["mapped"]]
        card["mapped_lists"] = mapped
        card["contact_count"] = sum(int(e["diallable_count"] or 0) for e in mapped)
    orphans = [e for e in lists if not e["mapped"]]
    selected_card = by_id.get(selected)
    return _tpl(
        request,
        "client/contacts_pick.html",
        ctx,
        agents=cards,
        orphans=orphans,
        selected=selected_card,
        error=request.query_params.get("error"),
    )


@router.post("/contacts/upload", dependencies=[Depends(check_csrf_form)])
async def upload_list(
    request: Request,
    ctx: Annotated[ClientContext, Depends(require_client_context)],
    agent_id: Annotated[uuid.UUID, Form()],
    file: UploadFile,
) -> Response:
    data = await file.read()
    filename = file.filename or "contacts.csv"
    try:
        parsed = contacts_service.parse_upload(filename, data)
    except contacts_service.UnreadableFile:
        return RedirectResponse(f"/contacts?agent={agent_id}&error=unreadable", status_code=303)
    async with request.app.state.db.client_session(ctx.client_account_id) as s:
        agent = await agents_service.get_agent(s, agent_id)
        if agent is None:
            return RedirectResponse("/contacts", status_code=303)
        contract = variable_contract(_content_of(agent))
        mapping = contacts_service.suggest_mapping(parsed.headers, contract)
        computed = contacts_service.compute_import(parsed, contract, mapping)
        list_id = await contacts_service.create_list(
            s,
            client_account_id=ctx.client_account_id,
            agent_id=agent_id,
            filename=filename,
            data=data,
            parsed=parsed,
            mapping=mapping,
            computed=computed,
        )
        await audit.record(
            s,
            actor_type=ctx.actor_type,
            actor_id=ctx.actor_id,
            action="imported_contact_list",
            client_account_id=ctx.client_account_id,
            target=str(list_id),
            meta={"filename": filename, "rows": len(parsed.rows)},
        )
        excluded = len(parsed.rows) - computed.diallable_count
        if excluded > 0:
            # FR-NOTIFY-2B: recorded even though the uploader is looking
            # at the review screen right now — the notification is for
            # the colleague who opens the app tomorrow.
            await notify.emit(
                s,
                event="import_blocked",
                title=notify.CLIENT_EVENTS["import_blocked"],
                body=(
                    f"{filename}: {excluded} of {len(parsed.rows)} rows cannot be"
                    " dialled. Review them before launch."
                ),
                client_account_id=ctx.client_account_id,
                agent_id=agent_id,
            )
    return RedirectResponse(f"/contacts/{list_id}", status_code=303)


# "How it will sound" (FR-CONTACT-7's three-row spoken preview) was
# removed by the user's call (12 Aug 2026): the script is behavioural
# direction for the conversation model, so no honest spoken rendering
# exists without asking an LLM to improvise one. May return in another
# form after the call-quality work pinned in issue #1.


async def _map_screen(
    request: Request,
    ctx: ClientContext,
    s: AsyncSession,
    entry: dict[str, Any],
    agent: dict[str, Any],
) -> HTMLResponse:
    content = _content_of(agent)
    contract = variable_contract(content)
    parsed = contacts_service.parse_upload(entry["filename"], entry["source_file"])
    mapping: contacts_service.Mapping = {h: entry["column_mapping"].get(h) for h in parsed.headers}
    health = await contacts_service.list_health(s, entry["id"])
    unmapped = contacts_service.unmapped_required(contract, mapping)
    phone_col = contacts_service.phone_column(mapping)
    unparseable_rows = (
        await s.execute(
            text(
                "SELECT row_index, phone_raw FROM contact"
                " WHERE contact_list_id = :lid AND exclusion_reason = 'unparseable_number'"
                " ORDER BY row_index LIMIT 5"
            ),
            {"lid": str(entry["id"])},
        )
    ).all()
    duplicates = entry["row_count"] - health["kept"]
    excluded = entry["row_count"] - entry["diallable_count"]
    mapped_count = sum(1 for v in mapping.values() if v is not None)
    return _tpl(
        request,
        "client/contacts_map.html",
        ctx,
        list=entry,
        agent=agent,
        headers=parsed.headers,
        mapping=mapping,
        contract=contract,
        unmapped=unmapped,
        phone_col=phone_col,
        health=health,
        duplicates=duplicates,
        excluded=excluded,
        mapped_count=mapped_count,
        unparseable_rows=unparseable_rows,
        error=request.query_params.get("error"),
    )


@router.get("/contacts/{list_id}", response_class=HTMLResponse)
async def map_screen(
    request: Request,
    ctx: Annotated[ClientContext, Depends(require_client_context)],
    list_id: uuid.UUID,
) -> Response:
    async with request.app.state.db.client_session(ctx.client_account_id) as s:
        entry = await contacts_service.get_list(s, list_id)
        if entry is None:
            return RedirectResponse("/contacts", status_code=303)
        agent = await agents_service.get_agent(s, entry["agent_id"])
        if agent is None:
            return RedirectResponse("/contacts", status_code=303)
        return await _map_screen(request, ctx, s, entry, agent)


def _mapping_from_form(
    headers: tuple[str, ...], form: dict[str, str], contract: tuple[Field, ...]
) -> contacts_service.Mapping:
    """col_<i> selects -> mapping. One phone column and one column per
    field; on a conflicting submission the first stays, the rest drop."""
    valid_ids = {f.id for f in contract}
    mapping: contacts_service.Mapping = {}
    phone_taken = False
    fields_taken: set[int] = set()
    for i, header in enumerate(headers):
        raw = form.get(f"col_{i}", "")
        target: contacts_service.ColumnTarget = None
        if raw == "phone" and not phone_taken:
            target, phone_taken = "phone", True
        elif raw.isdigit() and int(raw) in valid_ids and int(raw) not in fields_taken:
            target = int(raw)
            fields_taken.add(target)
        mapping[header] = target
    return mapping


@router.post("/contacts/{list_id}/mapping", dependencies=[Depends(check_csrf_form)])
async def save_mapping(
    request: Request,
    ctx: Annotated[ClientContext, Depends(require_client_context)],
    list_id: uuid.UUID,
) -> Response:
    form = {k: v for k, v in (await request.form()).items() if isinstance(v, str)}
    async with request.app.state.db.client_session(ctx.client_account_id) as s:
        entry = await contacts_service.get_list(s, list_id)
        if entry is None:
            return RedirectResponse("/contacts", status_code=303)
        agent = await agents_service.get_agent(s, entry["agent_id"])
        if agent is None:
            return RedirectResponse("/contacts", status_code=303)
        contract = variable_contract(_content_of(agent))
        parsed = contacts_service.parse_upload(entry["filename"], entry["source_file"])
        mapping = _mapping_from_form(parsed.headers, form, contract)
        computed = contacts_service.compute_import(parsed, contract, mapping)
        await contacts_service.save_mapping(
            s,
            list_id=list_id,
            client_account_id=ctx.client_account_id,
            mapping=mapping,
            computed=computed,
        )
        await audit.record(
            s,
            actor_type=ctx.actor_type,
            actor_id=ctx.actor_id,
            action="saved_contact_mapping",
            client_account_id=ctx.client_account_id,
            target=str(list_id),
        )
    return RedirectResponse(f"/contacts/{list_id}", status_code=303)


@router.post("/contacts/{list_id}/make-optional", dependencies=[Depends(check_csrf_form)])
async def make_field_optional(
    request: Request,
    ctx: Annotated[ClientContext, Depends(require_client_context)],
    list_id: uuid.UUID,
    field_id: Annotated[int, Form()],
    default: Annotated[str, Form()] = "",
) -> Response:
    """FR-CONTACT-5's second remedy for an unmapped required field.
    Optionals must carry a default that reads naturally when spoken
    (FR-CONTACT-9), so an empty one is refused."""
    if not default.strip():
        return RedirectResponse(f"/contacts/{list_id}?error=default", status_code=303)
    async with request.app.state.db.client_session(ctx.client_account_id) as s:
        entry = await contacts_service.get_list(s, list_id)
        if entry is None:
            return RedirectResponse("/contacts", status_code=303)
        agent = await agents_service.get_agent(s, entry["agent_id"])
        if agent is None:
            return RedirectResponse("/contacts", status_code=303)
        content = _content_of(agent)
        try:
            field = content.field_by_id(field_id)
        except KeyError:
            return RedirectResponse(f"/contacts/{list_id}", status_code=303)
        if field.kind != "input":
            return RedirectResponse(f"/contacts/{list_id}", status_code=303)
        new_content = replace(
            content,
            fields=tuple(
                replace(f, required=False, default=default.strip()) if f.id == field_id else f
                for f in content.fields
            ),
        )
        try:
            n = await agents_service.save_new_version(
                s,
                agent_id=entry["agent_id"],
                client_account_id=ctx.client_account_id,
                content=new_content,
            )
        except agents_service.AgentFrozen:
            return RedirectResponse(f"/contacts/{list_id}", status_code=303)
        contract = variable_contract(new_content)
        parsed = contacts_service.parse_upload(entry["filename"], entry["source_file"])
        mapping: contacts_service.Mapping = {
            h: entry["column_mapping"].get(h) for h in parsed.headers
        }
        computed = contacts_service.compute_import(parsed, contract, mapping)
        await contacts_service.save_mapping(
            s,
            list_id=list_id,
            client_account_id=ctx.client_account_id,
            mapping=mapping,
            computed=computed,
        )
        await audit.record(
            s,
            actor_type=ctx.actor_type,
            actor_id=ctx.actor_id,
            action="made_field_optional",
            client_account_id=ctx.client_account_id,
            target=str(entry["agent_id"]),
            meta={"field_id": field_id, "version": n},
        )
    return RedirectResponse(f"/contacts/{list_id}", status_code=303)


@router.post("/contacts/{list_id}/file", dependencies=[Depends(check_csrf_form)])
async def replace_file(
    request: Request,
    ctx: Annotated[ClientContext, Depends(require_client_context)],
    list_id: uuid.UUID,
    file: UploadFile,
) -> Response:
    data = await file.read()
    filename = file.filename or "contacts.csv"
    try:
        parsed = contacts_service.parse_upload(filename, data)
    except contacts_service.UnreadableFile:
        return RedirectResponse(f"/contacts/{list_id}?error=unreadable", status_code=303)
    async with request.app.state.db.client_session(ctx.client_account_id) as s:
        entry = await contacts_service.get_list(s, list_id)
        if entry is None:
            return RedirectResponse("/contacts", status_code=303)
        agent = await agents_service.get_agent(s, entry["agent_id"])
        if agent is None:
            return RedirectResponse("/contacts", status_code=303)
        contract = variable_contract(_content_of(agent))
        # Suggested afresh for new columns; a column the user already
        # placed keeps its target when the header survives the new file.
        mapping = contacts_service.suggest_mapping(parsed.headers, contract)
        mapping.update(
            {h: v for h, v in entry["column_mapping"].items() if h in mapping and v is not None}
        )
        computed = contacts_service.compute_import(parsed, contract, mapping)
        await contacts_service.replace_file(
            s,
            list_id=list_id,
            client_account_id=ctx.client_account_id,
            filename=filename,
            data=data,
            parsed=parsed,
            mapping=mapping,
            computed=computed,
        )
        await audit.record(
            s,
            actor_type=ctx.actor_type,
            actor_id=ctx.actor_id,
            action="replaced_contact_file",
            client_account_id=ctx.client_account_id,
            target=str(list_id),
            meta={"filename": filename, "rows": len(parsed.rows)},
        )
    return RedirectResponse(f"/contacts/{list_id}", status_code=303)
