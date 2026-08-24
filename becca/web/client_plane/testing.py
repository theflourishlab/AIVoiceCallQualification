"""The test screen: tune it until the answers are right (FR-TEST-1).

The loop lives on one screen: edit the extraction schema, place a real
test call with typed stand-in values, read what came back with a diff
against the previous test, describe what sounded wrong, regenerate, call
again. Results are pulled on view (and while a run is dialling the page
auto-refreshes), because insight events are not pushed to us.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text
from starlette.responses import Response

from becca.services import agents as agents_service
from becca.services import audit, testing
from becca.services import voice_config as voice_config_service
from becca.web.deps import ClientContext, check_csrf_form, require_client_context

router = APIRouter()


@router.get("/agents/{agent_id}/test", response_class=HTMLResponse)
async def test_screen(
    request: Request,
    ctx: Annotated[ClientContext, Depends(require_client_context)],
    agent_id: uuid.UUID,
) -> Response:
    gateway = request.app.state.telnyx
    settings = request.app.state.settings
    async with request.app.state.db.client_session(ctx.client_account_id) as s:
        agent = await agents_service.get_agent(s, agent_id)
        if agent is None:
            return RedirectResponse("/", status_code=303)
        await testing.fetch_pending_results(s, gateway, agent_id=agent_id)
        runs = await testing.list_test_runs(s, agent_id)
        rate_per_min = (
            await s.execute(
                text("SELECT rate_per_min_usd FROM client_account WHERE id = :cid"),
                {"cid": str(ctx.client_account_id)},
            )
        ).scalar_one()
        # Real settled money, not an estimate — the ledger is what the
        # client was actually billed for this agent's tests.
        spend = (
            await s.execute(
                text(
                    "SELECT coalesce(-sum(wl.amount_usd), 0) FROM wallet_ledger wl"
                    " JOIN test_run t ON t.id = wl.test_run_id WHERE t.agent_id = :aid"
                ),
                {"aid": str(agent_id)},
            )
        ).scalar_one()

    content = agent["content"]
    by_n = {r["n"]: r for r in runs}
    for r in runs:
        prev = by_n.get(r["n"] - 1)
        r["diffs"] = testing.diff_results(
            content, r["results"] or None, (prev or {}).get("results") or None
        )
    from becca.domain.codec import content_to_json
    from becca.domain.views import insight_schema, variable_contract

    last_stand_ins = runs[0]["stand_ins"] if runs else {}
    dialling = any(r["status"] == "dialling" for r in runs)
    result: HTMLResponse = request.app.state.templates.TemplateResponse(
        request,
        "client/agent_test.html",
        {
            "plane": "client",
            "ctx": ctx,
            "agent": agent,
            "content": content,
            "inputs": variable_contract(content),
            "outputs": insight_schema(content),
            "field_data": content_to_json(content)["fields"],
            "base_content": content_to_json(content),
            "runs": runs,
            "next_n": (runs[0]["n"] + 1) if runs else 1,
            "last_stand_ins": last_stand_ins,
            "last_to_number": runs[0]["to_number"] if runs else "",
            "rate_per_min": float(rate_per_min),
            "estimated_price": round(float(rate_per_min) * settings.estimated_call_minutes, 2),
            "spend": float(spend),
            "dialling": dialling,
            # FR-LAUNCH-7: the same condition save_new_version enforces.
            # The screen must not offer edits the service will refuse.
            "frozen": agent["status"] not in ("draft", "tested"),
            "from_number_missing": (
                settings.telnyx_mode == "real" and not settings.telnyx_from_number
            ),
            "error": request.query_params.get("error"),
        },
    )
    return result


@router.post("/agents/{agent_id}/test-calls", dependencies=[Depends(check_csrf_form)])
async def place_test_call(
    request: Request,
    ctx: Annotated[ClientContext, Depends(require_client_context)],
    agent_id: uuid.UUID,
    to_number: Annotated[str, Form()],
) -> Response:
    settings = request.app.state.settings
    gateway = request.app.state.telnyx
    if settings.telnyx_mode == "real" and not settings.telnyx_from_number:
        return RedirectResponse(f"/agents/{agent_id}/test?error=no-from-number", status_code=303)
    form = await request.form()
    stand_ins = {
        key.removeprefix("standin_"): str(value)
        for key, value in form.items()
        if key.startswith("standin_") and isinstance(value, str)
    }
    # Reserve-then-place, in the dispatcher's SD-27 shape: transaction 1
    # takes the wallet hold (the test_run row), transaction 2 dials. A
    # crash between the two leaves a reserved row that housekeeping
    # fails at 15 minutes — never an untracked Telnyx call.
    async with request.app.state.db.client_session(ctx.client_account_id) as s:
        agent = await agents_service.get_agent(s, agent_id)
        if agent is None:
            return RedirectResponse("/", status_code=303)
        recording = (
            await s.execute(
                text("SELECT recording_enabled FROM client_account WHERE id = :cid"),
                {"cid": str(ctx.client_account_id)},
            )
        ).scalar_one()
        test_run_id = await testing.reserve_test_run(
            s,
            agent_id=agent_id,
            client_account_id=ctx.client_account_id,
            agent_version_id=agent["version_id"],
            content=agent["content"],
            to_number=to_number.strip(),
            stand_ins=stand_ins,
            wallet_floor_usd=settings.wallet_floor_usd,
        )
        if test_run_id is None:
            return RedirectResponse(f"/agents/{agent_id}/test?error=balance", status_code=303)
        await audit.record(
            s,
            actor_type=ctx.actor_type,
            actor_id=ctx.actor_id,
            action="placed_test_call",
            client_account_id=ctx.client_account_id,
            target=str(agent_id),
        )
    try:
        async with request.app.state.db.client_session(ctx.client_account_id) as s:
            await testing.dial_test_run(
                s,
                gateway,
                test_run_id=test_run_id,
                agent_id=agent_id,
                content=agent["content"],
                to_number=to_number.strip(),
                stand_ins=stand_ins,
                from_number=settings.telnyx_from_number or "+2340000000000",
                record=bool(recording),
                voice_config=voice_config_service.resolve(agent["voice_config"], settings),
                webhook_base=settings.public_webhook_base.rstrip("/"),
            )
    except Exception as exc:
        # The dial never went out (or its outcome is unknowable) — the
        # transaction above rolled back, so release the reservation in a
        # fresh one and tell the user rather than 500ing mid-loop.
        import sentry_sdk

        print(f"test dial failed for agent {agent_id}: {exc!r}", flush=True)
        sentry_sdk.capture_exception(exc)
        async with request.app.state.db.client_session(ctx.client_account_id) as s:
            await testing.fail_test_run(s, test_run_id=test_run_id)
        return RedirectResponse(f"/agents/{agent_id}/test?error=dial", status_code=303)
    return RedirectResponse(f"/agents/{agent_id}/test", status_code=303)


@router.post("/agents/{agent_id}/regenerate", dependencies=[Depends(check_csrf_form)])
async def regenerate(
    request: Request,
    ctx: Annotated[ClientContext, Depends(require_client_context)],
    agent_id: uuid.UUID,
    problem: Annotated[str, Form()],
    return_to: Annotated[str, Form()] = "",
) -> Response:
    generator = request.app.state.generator
    back = f"/agents/{agent_id}" if return_to == "review" else f"/agents/{agent_id}/test"
    async with request.app.state.db.client_session(ctx.client_account_id) as s:
        agent = await agents_service.get_agent(s, agent_id)
        if agent is None:
            return RedirectResponse("/", status_code=303)
    # FR-LAUNCH-7: checked BEFORE the generator runs — a frozen agent
    # must not burn a generation call only for save_new_version to
    # refuse the result (this path 500ed on finished agents).
    if agent["status"] not in ("draft", "tested"):
        return RedirectResponse(back, status_code=303)
    new_content = await generator.regenerate(current=agent["content"], problem=problem)
    async with request.app.state.db.client_session(ctx.client_account_id) as s:
        n = await agents_service.save_new_version(
            s, agent_id=agent_id, client_account_id=ctx.client_account_id, content=new_content
        )
        await audit.record(
            s,
            actor_type=ctx.actor_type,
            actor_id=ctx.actor_id,
            action="regenerated_agent",
            client_account_id=ctx.client_account_id,
            target=str(agent_id),
            meta={"version": n, "problem": problem[:200]},
        )
    return RedirectResponse(back, status_code=303)
