"""One dispatch tick (FR-DISPATCH-2): dial what may be dialled, now.

Per runnable agent: the pause flag is read fresh from the row
(FR-DISPATCH-6), the calling window is a clock check (domain.windows),
the spend cap is an estimate against completed-plus-in-flight calls
(FR-DISPATCH-10), and the claim itself is the database governor
(worker/claims.py, FR-DISPATCH-9). The call is placed AFTER the
claiming transaction commits, so a crash between the two leaves a
reconcilable 'dialling' row and never a double dial (SD-27) —
housekeeping times those out.
"""

import json
import uuid
from datetime import datetime, time
from typing import Any

from sqlalchemy import text

from becca.config import Settings
from becca.db.session import SessionFactory
from becca.domain import windows
from becca.domain.codec import content_from_json
from becca.domain.model import AgentVersionContent
from becca.domain.serialize import dynamic_variable_keys
from becca.domain.views import variable_contract
from becca.services import contacts as contacts_service
from becca.services import notify
from becca.telnyx.gateway import AmbiguousDialError, DialRefused, TelnyxError, TelnyxGateway

from . import claims


def _jsonish(value: Any) -> Any:
    return value if isinstance(value, dict | list) else json.loads(value)


async def _runnable_agents(db: SessionFactory) -> list[dict[str, Any]]:
    async with db.worker_session() as s:
        rows = await s.execute(
            text(
                """
                SELECT a.id, a.client_account_id, a.status,
                       a.telnyx_run_assistant_id, a.telnyx_run_texml_app_id,
                       rs.window_start, rs.window_end, rs.days, rs.excluded_bands,
                       rs.timezone, rs.spend_cap,
                       ca.recording_enabled, ca.rate_per_min_usd, ca.channel_allocation,
                       (SELECT n.phone_e164 FROM phone_number n
                         WHERE n.client_account_id = a.client_account_id
                           AND n.number_use = 'outbound'
                         ORDER BY n.assigned_at DESC LIMIT 1)
                  FROM agent a
                  JOIN run_schedule rs ON rs.agent_id = a.id
                  JOIN client_account ca ON ca.id = a.client_account_id
                 WHERE a.status IN ('scheduled', 'dialling')
                   AND NOT rs.paused
                   AND (rs.not_before IS NULL OR rs.not_before <= now())
                """
            )
        )
        return [
            {
                "agent_id": r[0],
                "client_account_id": r[1],
                "status": r[2],
                "assistant_id": r[3],
                "texml_app_id": r[4],
                "window": windows.CallingWindow(
                    window=windows.TimeBand(start=r[5], end=r[6]),
                    days=frozenset(int(d) for d in r[7]),
                    excluded_bands=tuple(
                        windows.TimeBand(
                            start=time.fromisoformat(b["start"]),
                            end=time.fromisoformat(b["end"]),
                        )
                        for b in _jsonish(r[8])
                    ),
                    timezone=r[9],
                ),
                "spend_cap": float(r[10]),
                "record": bool(r[11]),
                "rate_per_min": float(r[12]),
                "channel_allocation": int(r[13]),
                # Phase 6: the caller ID is the client's assigned number.
                # NULL for runs launched before the inventory existed —
                # those fall back to the configured from-number at dial.
                "from_number": r[14],
            }
            for r in rows
        ]


async def _pause_run(
    db: SessionFactory, agent_id: uuid.UUID, reason: str, *, event: str = "run_halted"
) -> None:
    async with db.worker_session() as s:
        await s.execute(
            text("UPDATE run_schedule SET paused = true WHERE agent_id = :aid"),
            {"aid": str(agent_id)},
        )
        # FR-NOTIFY-2B: the client discovers the halt when they next open
        # the app — fail-safe, nothing dials while unread. One row per
        # event per audience regardless of how many ticks re-observe the
        # paused condition (the pause flag stops re-observation anyway).
        row = (
            await s.execute(
                text("SELECT client_account_id, name FROM agent WHERE id = :aid"),
                {"aid": str(agent_id)},
            )
        ).first()
        if row is not None:
            await notify.emit(
                s,
                event=event,
                title=notify.CLIENT_EVENTS[event],
                body=f"{row[1]}: {reason}",
                client_account_id=row[0],
                agent_id=agent_id,
            )
        await s.execute(
            text(
                "INSERT INTO audit_log (actor_type, actor_id, client_account_id, action,"
                " target, metadata)"
                " SELECT 'staff', '00000000-0000-0000-0000-000000000000', client_account_id,"
                " 'run_paused_by_worker', :target, :meta FROM agent WHERE id = :aid"
            ),
            {"target": str(agent_id), "aid": str(agent_id), "meta": json.dumps({"reason": reason})},
        )


async def _spend_reached(db: SessionFactory, run: dict[str, Any], settings: Settings) -> bool:
    """The client's per-run brake, in real money since the wallet: this
    run's settled ledger debits plus a full hold for every call still in
    flight (conservative, FR-BILL-9's quote-high spirit). The wallet is
    the hard gate; this cap is per-run intent."""
    from becca.services import wallet

    hold = float(wallet.per_call_reserve(run["rate_per_min"], settings.max_call_minutes))
    async with db.worker_session() as s:
        row = (
            await s.execute(
                text(
                    """
                    SELECT coalesce((SELECT -sum(wl.amount_usd) FROM wallet_ledger wl
                              JOIN call c ON c.id = wl.call_id
                             WHERE c.agent_id = :aid), 0),
                           (SELECT count(*) FROM call
                             WHERE agent_id = :aid AND status = 'dialling')
                    """
                ),
                {"aid": str(run["agent_id"])},
            )
        ).one()
    settled, in_flight = float(row[0]), int(row[1])
    return bool(settled + in_flight * hold >= float(run["spend_cap"]))


async def _wallet_too_low(db: SessionFactory, run: dict[str, Any], settings: Settings) -> bool:
    """Belt to claim_one's suspenders: the claim gate refuses atomically,
    this pre-check is what pauses the run and tells the owner. A race
    where this passes and the claim still refuses just ends the tick;
    the next tick lands here and pauses."""
    from becca.services import wallet

    async with db.worker_session() as s:
        avail = await wallet.available(
            s,
            client_account_id=run["client_account_id"],
            max_call_minutes=settings.max_call_minutes,
        )
    return float(avail) < float(
        wallet.per_call_reserve(run["rate_per_min"], settings.max_call_minutes)
    )


async def _load_dial_context(
    db: SessionFactory, run: dict[str, Any], contact_id: uuid.UUID
) -> tuple[AgentVersionContent, dict[str, str], str]:
    async with db.worker_session() as s:
        agent_row = (
            await s.execute(
                text(
                    "SELECT v.fields, v.script_blocks FROM agent a"
                    " JOIN agent_version v ON v.id = a.current_version_id WHERE a.id = :aid"
                ),
                {"aid": str(run["agent_id"])},
            )
        ).one()
        contact = (
            await s.execute(
                text("SELECT phone_e164, variables FROM contact WHERE id = :cid"),
                {"cid": str(contact_id)},
            )
        ).one()
    content = content_from_json(
        {"fields": _jsonish(agent_row[0]), "script_blocks": _jsonish(agent_row[1])}
    )
    raw_vars = {str(k): str(v) for k, v in _jsonish(contact[1]).items()}
    resolved = contacts_service.resolved_variables(variable_contract(content), raw_vars)
    by_key = {content.field_by_id(fid).key: value for fid, value in resolved.items()}
    return content, by_key, str(contact[0])


async def _record_outcome(
    db: SessionFactory,
    *,
    call_id: uuid.UUID,
    call_sid: str | None,
    failed: bool,
    queue_item_id: uuid.UUID,
    retry: bool,
) -> None:
    from becca.services import ingest

    async with db.worker_session() as s:
        if failed:
            await s.execute(
                text("UPDATE call SET status = 'failed', ended_at = now() WHERE id = :id"),
                {"id": str(call_id)},
            )
            if retry:
                await ingest.apply_retry_policy(s, queue_item_id=queue_item_id)
            else:
                await s.execute(
                    text(
                        "UPDATE queue_item SET state = 'skipped'"
                        " WHERE id = :qid AND state = 'dialling'"
                    ),
                    {"qid": str(queue_item_id)},
                )
        else:
            await s.execute(
                text(
                    "UPDATE call SET telnyx_call_control_id = :sid, started_at = now()"
                    " WHERE id = :id"
                ),
                {"sid": call_sid, "id": str(call_id)},
            )


async def dispatch_tick(
    db: SessionFactory,
    gateway: TelnyxGateway,
    settings: Settings,
    *,
    now_utc: datetime,
) -> int:
    """Returns the number of calls placed this tick."""
    placed = 0
    for run in await _runnable_agents(db):
        if not windows.is_open(run["window"], now_utc):
            continue
        if await _spend_reached(db, run, settings):
            await _pause_run(db, run["agent_id"], "spend cap reached", event="spend_cap_reached")
            continue
        if await _wallet_too_low(db, run, settings):
            await _pause_run(
                db,
                run["agent_id"],
                "wallet balance too low to cover the next call — top up to resume",
                event="run_paused_no_balance",
            )
            continue
        if run["status"] == "scheduled":
            async with db.worker_session() as s:
                await s.execute(
                    text("UPDATE agent SET status = 'dialling' WHERE id = :aid"),
                    {"aid": str(run["agent_id"])},
                )

        while True:
            async with db.worker_session() as s:
                claim = await claims.claim_one(
                    s,
                    client_account_id=run["client_account_id"],
                    agent_id=run["agent_id"],
                    max_call_minutes=settings.max_call_minutes,
                )
            if claim is None or claim.contact_id is None:
                break

            content, variables, to_number = await _load_dial_context(db, run, claim.contact_id)

            # FR-DISPATCH-11: an invariant assertion, not a control. A
            # dynamic-variable key with no value would reach the contact
            # as literal mustache. It cannot happen given FR-AGENT-3 and
            # FR-CONTACT-9 — if it does, the domain model was violated
            # upstream: stop the run, never skip the row.
            missing = [k for k in dynamic_variable_keys(content) if not variables.get(k)]
            if missing:
                from becca import obs

                await _record_outcome(
                    db,
                    call_id=claim.call_id,
                    call_sid=None,
                    failed=True,
                    queue_item_id=claim.queue_item_id,
                    retry=False,
                )
                await _pause_run(db, run["agent_id"], f"unresolved variables: {', '.join(missing)}")
                obs.page(
                    "FR-DISPATCH-11 assertion fired: unresolved variables"
                    f" {missing} on agent {run['agent_id']}"
                )
                break

            webhook_base = settings.public_webhook_base.rstrip("/")
            callback = f"{webhook_base}/webhooks/telnyx" if webhook_base else ""
            try:
                response = await gateway.place_call(
                    connection_id=run["texml_app_id"],
                    assistant_id=run["assistant_id"],
                    to=to_number,
                    from_=run["from_number"] or settings.telnyx_from_number or "+2340000000000",
                    variables=variables,
                    metadata={
                        "agent_id": str(run["agent_id"]),
                        "contact_id": str(claim.contact_id),
                        "queue_item_id": str(claim.queue_item_id),
                    },
                    record=run["record"],  # the client's setting NOW (FR-REC-2)
                    status_callback=callback,
                    amd_status_callback=callback,
                )
            except DialRefused as exc:
                # SD-13 fired: a non-production worker tried to dial a
                # stranger. Not a per-row condition — stop the run.
                await _record_outcome(
                    db,
                    call_id=claim.call_id,
                    call_sid=None,
                    failed=True,
                    queue_item_id=claim.queue_item_id,
                    retry=False,
                )
                await _pause_run(db, run["agent_id"], f"dial refused: {exc.to}")
                break
            except AmbiguousDialError:
                # The call may exist (SD-16). Leave the row 'dialling';
                # housekeeping reconciles it by timeout, never a redial.
                break
            except Exception as exc:
                # A dial that failed before send is retryable, but NEVER
                # silently: an autonomous dialler that eats its own
                # errors is undebuggable (learned live, 12 Aug 2026 —
                # four mystery failures with nothing in any log).
                import sentry_sdk

                print(f"dial failed for agent {run['agent_id']}: {exc!r}", flush=True)
                sentry_sdk.capture_exception(exc)
                if isinstance(exc, TelnyxError) and exc.status_code == 403:
                    # Account-level refusal (spike findings §10: empty
                    # balance presents as D17 "Account is disabled" —
                    # 403 on the dial while every CRUD endpoint keeps
                    # working). Not a per-contact condition: refund the
                    # claim entirely — no retry budget burned, the
                    # contact was never rung — and pause the RUN. The
                    # audit row carries the reason; a human resumes
                    # after fixing the account.
                    await _refund_claim(db, claim)
                    await _pause_run(
                        db,
                        run["agent_id"],
                        f"telnyx refused the account (403): {exc.body[:200]}",
                    )
                    break
                await _record_outcome(
                    db,
                    call_id=claim.call_id,
                    call_sid=None,
                    failed=True,
                    queue_item_id=claim.queue_item_id,
                    retry=True,
                )
                break

            await _record_outcome(
                db,
                call_id=claim.call_id,
                call_sid=str(response.get("call_sid", "")) or None,
                failed=False,
                queue_item_id=claim.queue_item_id,
                retry=False,
            )
            placed += 1

        await _finish_if_drained(db, run["agent_id"])

    # FR-NOTIFY-2B: channel pool exhausted is Becca's to know — every
    # account-wide channel is speaking at once, so any further demand
    # queues. Deduped to one row a day; the condition recurs every tick
    # while a full-allocation run dials.
    async with db.worker_session() as s:
        in_flight = (
            await s.execute(text("SELECT count(*) FROM call WHERE status = 'dialling'"))
        ).scalar_one()
        if int(in_flight) >= settings.channel_ceiling:
            await notify.emit(
                s,
                event="pool_exhausted",
                title=notify.STAFF_EVENTS["pool_exhausted"],
                body=f"All {settings.channel_ceiling} account channels are in use.",
                dedupe_hours=24,
            )
    return placed


async def _refund_claim(db: SessionFactory, claim: claims.Claim) -> None:
    """Undo a claim whose dial was refused account-wide: delete the call
    row (no call happened — Telnyx refused before any ring) and put the
    queue item back exactly as it was, attempt refunded. Deleting the
    call row is what makes the refund safe: the next claim re-issues the
    same attempt number, whose idempotency key must be free. The wallet
    ledger keys on call.id (never the idempotency key) for exactly this
    reason, and the status = 'dialling' guard means a settled call can
    never match — its FK from wallet_ledger would refuse anyway."""
    async with db.worker_session() as s:
        await s.execute(
            text("DELETE FROM call WHERE id = :id AND status = 'dialling'"),
            {"id": str(claim.call_id)},
        )
        await s.execute(
            text(
                "UPDATE queue_item SET state = 'pending',"
                " attempts = greatest(attempts - 1, 0)"
                " WHERE id = :qid AND state = 'dialling'"
            ),
            {"qid": str(claim.queue_item_id)},
        )


async def _finish_if_drained(db: SessionFactory, agent_id: uuid.UUID) -> None:
    """FR-DISPATCH-7's natural twin: when nothing is pending or in
    flight, the run is finished. The run assistant is retained."""
    async with db.worker_session() as s:
        remaining = (
            await s.execute(
                text(
                    "SELECT count(*) FROM queue_item WHERE agent_id = :aid"
                    " AND state IN ('pending', 'dialling')"
                ),
                {"aid": str(agent_id)},
            )
        ).scalar_one()
        if int(remaining) == 0:
            finished = (
                await s.execute(
                    text(
                        "UPDATE agent SET status = 'finished'"
                        " WHERE id = :aid AND status = 'dialling'"
                        " RETURNING client_account_id, name"
                    ),
                    {"aid": str(agent_id)},
                )
            ).first()
            if finished is not None:
                # RETURNING guarantees exactly one emission: the update
                # only matches while status is 'dialling', once.
                await notify.emit(
                    s,
                    event="run_finished",
                    title=notify.CLIENT_EVENTS["run_finished"],
                    body=f"{finished[1]} has called everyone on its list. Results are ready.",
                    client_account_id=finished[0],
                    agent_id=agent_id,
                )
