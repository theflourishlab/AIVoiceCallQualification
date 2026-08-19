"""Housekeeping: a counter in the worker's loop, not a scheduler (SD-22).

Two duties. The scratch sweeper (FR-LAUNCH-5/6): once an agent has
launched or finished, its scratch assistant is an orphan — delete the
assistant AND its auto-created TeXML application, in that order,
because deletion does not cascade (verified live, twice). And
stuck-'dialling' reconciliation: status callbacks have been observed to
simply stop arriving (spike-era mystery, unresolved), and a crash
between claim-commit and dial leaves the same shape — a call that never
ends. Both would wedge a governor channel forever, so calls dialling
past a hard timeout are closed as failed and their queue rows get the
normal retry policy.
"""

from sqlalchemy import text

from becca.db.session import SessionFactory
from becca.services import ingest
from becca.telnyx.gateway import TelnyxGateway

# A legitimate AI call is capped well under this by the assistant's own
# time limit; anything older is a lost callback or a crashed dial.
STUCK_DIALLING_MINUTES = 15


async def sweep_scratch_assistants(db: SessionFactory, gateway: TelnyxGateway) -> int:
    """Delete scratch assistants of agents that are past testing.
    Failures leave the ids in place — the next pass retries; a cleanup
    failure can never abort anything (FR-LAUNCH-5)."""
    async with db.worker_session() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT id, telnyx_scratch_assistant_id, telnyx_scratch_texml_app_id"
                    " FROM agent WHERE telnyx_scratch_assistant_id IS NOT NULL"
                    " AND status NOT IN ('draft', 'tested')"
                )
            )
        ).all()
    swept = 0
    for agent_id, assistant_id, texml_app_id in rows:
        try:
            await gateway.delete_assistant(assistant_id=assistant_id)
            if texml_app_id:
                await gateway.delete_texml_application(texml_app_id=texml_app_id)
        except Exception as exc:
            # Next pass retries; a cleanup failure never propagates
            # (FR-LAUNCH-5). Logged so a permanently failing sweep is
            # visible rather than silently eternal.
            print(f"scratch sweep failed for agent {agent_id}: {exc!r}", flush=True)
            continue
        async with db.worker_session() as s:
            await s.execute(
                text(
                    "UPDATE agent SET telnyx_scratch_assistant_id = NULL,"
                    " telnyx_scratch_texml_app_id = NULL,"
                    " telnyx_scratch_insight_map = '{}' WHERE id = :aid"
                ),
                {"aid": str(agent_id)},
            )
        swept += 1
    return swept


async def reconcile_stuck_dialling(db: SessionFactory) -> int:
    """Close call rows that will never hear their callback."""
    async with db.worker_session() as s:
        stuck = (
            await s.execute(
                text(
                    """
                    SELECT c.id, c.queue_item_id FROM call c
                      JOIN queue_item qi ON qi.id = c.queue_item_id
                     WHERE c.status = 'dialling'
                       AND qi.last_attempt_at < now() - make_interval(mins => :mins)
                    """
                ),
                {"mins": STUCK_DIALLING_MINUTES},
            )
        ).all()
        for call_id, queue_item_id in stuck:
            await s.execute(
                text("UPDATE call SET status = 'failed', ended_at = now() WHERE id = :id"),
                {"id": str(call_id)},
            )
            await ingest.apply_retry_policy(s, queue_item_id=queue_item_id)
    return len(stuck)


async def fail_stale_test_runs(db: SessionFactory) -> int:
    """Close test runs that will never resolve. The test screen's own
    polling only runs while someone watches the page; since the wallet
    (14 Aug 2026) a 'dialling' test_run holds a reservation, so an
    unwatched one must not reserve forever. 15 min sits safely past the
    screen's 8-minute absolute timeout, so this sweep never races the
    polling loop's verdict — it only catches abandoned runs."""
    async with db.worker_session() as s:
        rows = (
            await s.execute(
                text(
                    "UPDATE test_run SET status = 'failed'"
                    " WHERE status = 'dialling'"
                    " AND created_at < now() - make_interval(mins => :mins)"
                    " RETURNING id"
                ),
                {"mins": STUCK_DIALLING_MINUTES},
            )
        ).all()
    return len(rows)


async def housekeeping_tick(db: SessionFactory, gateway: TelnyxGateway) -> None:
    await sweep_scratch_assistants(db, gateway)
    await reconcile_stuck_dialling(db)
    await fail_stale_test_runs(db)
