"""Minimal webhook ingest: enough lifecycle to keep the dispatcher
honest. Phase 5 grows this into the full results pipeline.

The governor counts calls in 'dialling' (FR-DISPATCH-9), so a call that
never leaves that state wedges a channel forever. This module's whole
job is moving call rows out of 'dialling' when Telnyx reports an
outcome, and applying the retry policy (FR-DISPATCH-8) to the queue
row. `AnsweredBy` is stored when present — optional enrichment only; an
AMD event has never been observed live (spike findings §9), so nothing
here or in the worker depends on one arriving.

Idempotency (SD-26): TeXML callbacks carry no event id, so the ledger
key is synthesised from (CallSid, source, sequence, status); INSERT
.. ON CONFLICT DO NOTHING makes redelivery a no-op.
"""

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# CallStatus values that end a call attempt without a conversation.
_FAILURES = {"failed", "busy", "no-answer", "canceled"}


async def ingest_texml_callback(
    session: AsyncSession, body: dict[str, Any], *, max_call_minutes: int = 15
) -> bool:
    """Apply one TeXML status callback. Returns True if state changed.

    This is also where the wallet SETTLES (14 Aug 2026): the guarded
    dialling->completed transition has the call's duration in hand and
    fires at most once, so the debit is atomic with the state change
    and idempotent under redelivery (three independent guards: the
    webhook_event dedupe, the status transition, and the ledger's
    unique index — any one suffices).

    A CallSid with no call row is tried against test_run.telnyx_call_sid
    — test calls are billed like any call, and this is where. The
    webhook owns only their duration_sec and settlement; test_run.status
    stays owned by the polling loop in services/testing.py.
    """
    call_sid = str(body.get("CallSid", ""))
    status = str(body.get("CallStatus", ""))
    if not call_sid or not status:
        return False

    ledger_key = (
        f"texml:{call_sid}:{body.get('CallbackSource', '')}"
        f":{body.get('SequenceNumber', '')}:{status}"
    )
    inserted = await session.execute(
        text(
            "INSERT INTO webhook_event (telnyx_event_id, event_type, payload)"
            " VALUES (:key, 'texml_status', :payload) ON CONFLICT DO NOTHING"
        ),
        {"key": ledger_key, "payload": json.dumps(body)},
    )
    if inserted.rowcount == 0:  # type: ignore[attr-defined]  # CursorResult at runtime
        return False  # redelivery — already applied

    call = (
        await session.execute(
            text(
                "SELECT id, status, queue_item_id, client_account_id FROM call"
                " WHERE telnyx_call_control_id = :sid"
            ),
            {"sid": call_sid},
        )
    ).first()
    if call is None:
        return await _ingest_test_call(
            session, body, call_sid=call_sid, status=status, max_call_minutes=max_call_minutes
        )
    call_id, current_status, queue_item_id, client_account_id = call

    # Enrichment that any event may carry. RecordingSid is stored as an
    # ID only, never a URL (FR-REC-3); untested live — no recorded call
    # has run yet.
    session_id = str(body.get("CallSessionId", "")) or None
    answered_by = str(body.get("AnsweredBy", "")) or None
    recording_id = str(body.get("RecordingSid", "")) or None
    await session.execute(
        text(
            "UPDATE call SET"
            " telnyx_call_session_id = coalesce(telnyx_call_session_id, :session),"
            " answered_by = coalesce(:answered, answered_by),"
            " telnyx_recording_id = coalesce(:rec, telnyx_recording_id)"
            " WHERE id = :id"
        ),
        {"session": session_id, "answered": answered_by, "rec": recording_id, "id": call_id},
    )

    if status in ("answered", "in-progress"):
        await session.execute(
            text("UPDATE call SET started_at = coalesce(started_at, now()) WHERE id = :id"),
            {"id": call_id},
        )
        return True

    if status == "completed" and current_status == "dialling":
        duration = int(body.get("CallDuration") or 0)
        await session.execute(
            text(
                "UPDATE call SET status = 'completed', duration_sec = :dur,"
                " ended_at = now() WHERE id = :id"
            ),
            {"dur": duration, "id": call_id},
        )
        await session.execute(
            text("UPDATE queue_item SET state = 'done' WHERE id = :qid"),
            {"qid": queue_item_id},
        )
        from becca.services import wallet

        if await wallet.settle_call(session, call_id=call_id) is not None:
            await _warn_if_wallet_low(
                session, client_account_id=client_account_id, max_call_minutes=max_call_minutes
            )
        await _notify(session, call_id)
        return True

    if status in _FAILURES and current_status == "dialling":
        await session.execute(
            text("UPDATE call SET status = 'failed', ended_at = now() WHERE id = :id"),
            {"id": call_id},
        )
        await apply_retry_policy(session, queue_item_id=queue_item_id)
        await _notify(session, call_id)
        return True

    return False


async def _ingest_test_call(
    session: AsyncSession,
    body: dict[str, Any],
    *,
    call_sid: str,
    status: str,
    max_call_minutes: int,
) -> bool:
    """Billing-only ingest for test calls. duration_sec is coalesced
    (first callback wins) and settlement rides the ledger's unique
    index — the status column stays untouched, because the polling loop
    owns it and its transitions are not this webhook's business."""
    if status != "completed":
        return False
    row = (
        await session.execute(
            text("SELECT id, client_account_id FROM test_run WHERE telnyx_call_sid = :sid"),
            {"sid": call_sid},
        )
    ).first()
    if row is None:
        return False
    test_run_id, client_account_id = row
    duration = int(body.get("CallDuration") or 0)
    await session.execute(
        text("UPDATE test_run SET duration_sec = coalesce(duration_sec, :dur) WHERE id = :id"),
        {"dur": duration, "id": test_run_id},
    )
    from becca.services import wallet

    if await wallet.settle_test_call(session, test_run_id=test_run_id) is not None:
        await _warn_if_wallet_low(
            session, client_account_id=client_account_id, max_call_minutes=max_call_minutes
        )
    return True


async def _warn_if_wallet_low(
    session: AsyncSession, *, client_account_id: Any, max_call_minutes: int
) -> None:
    """Capacity-relative warning line: below two full dialling waves of
    holds, the owner is within sight of the hard pause. Deduped to one
    row a day per audience — settles recur every few minutes while a
    run drains a low wallet."""
    from becca.services import notify, wallet

    row = (
        await session.execute(
            text(
                "SELECT name, wallet_balance_usd, rate_per_min_usd, channel_allocation"
                " FROM client_account WHERE id = :cid"
            ),
            {"cid": str(client_account_id)},
        )
    ).first()
    if row is None:
        return
    name, balance, rate, allocation = row
    line = wallet.low_balance_line(
        channel_allocation=int(allocation),
        rate_per_min_usd=rate,
        max_call_minutes=max_call_minutes,
    )
    if balance >= line:
        return
    await notify.emit(
        session,
        event="wallet_low",
        title=notify.CLIENT_EVENTS["wallet_low"],
        body=f"Your wallet is at ${float(balance):.2f}. Top up to keep your agents dialling.",
        client_account_id=client_account_id,
        dedupe_hours=24,
    )
    await notify.emit(
        session,
        event="client_wallet_low",
        title=notify.STAFF_EVENTS["client_wallet_low"],
        body=f"{name}'s wallet is at ${float(balance):.2f}.",
        dedupe_hours=24,
    )


async def _notify(session: AsyncSession, call_id: Any) -> None:
    """Wake the SSE monitor (SD-09: Postgres is the pub/sub)."""
    await session.execute(
        text("SELECT pg_notify('run_progress', (SELECT agent_id::text FROM call WHERE id = :id))"),
        {"id": call_id},
    )


async def apply_retry_policy(session: AsyncSession, *, queue_item_id: Any) -> None:
    """FR-DISPATCH-8: retry_attempts extra tries at retry_interval_secs,
    each waiting for the calling window like any other pending item.
    attempts was incremented at claim time, so the row has been tried
    `attempts` times already; it goes back to pending while
    attempts < 1 + retry_attempts."""
    await session.execute(
        text(
            """
            UPDATE queue_item qi
               SET state = CASE
                     WHEN qi.attempts < 1 + coalesce(rs.retry_attempts, 0) THEN 'pending'
                     ELSE 'failed'
                   END,
                   next_attempt_at = now()
                     + make_interval(secs => coalesce(rs.retry_interval_secs, 3600))
              FROM run_schedule rs
             WHERE rs.agent_id = qi.agent_id
               AND qi.id = :qid
               AND qi.state = 'dialling'
            """
        ),
        {"qid": queue_item_id},
    )
