"""The concurrency governor, in the database (FR-DISPATCH-9, SD-09).

The allocation is enforced by the same mechanism that records the calls,
so the two cannot disagree, and it is correct no matter how many worker
processes exist (deploy overlap, SD-09).

Claiming locks the client_account row first. That serialises claims per
client, which is what makes the dialling-count read safe: under READ
COMMITTED, two concurrent claimers for the same client would otherwise
both see the same count and both dial. Different clients' claims do not
contend — each locks its own row.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class Claim:
    queue_item_id: uuid.UUID
    call_id: uuid.UUID
    contact_id: uuid.UUID | None
    idempotency_key: str


async def claim_one(
    session: AsyncSession,
    *,
    client_account_id: uuid.UUID,
    agent_id: uuid.UUID,
    max_call_minutes: int,
) -> Claim | None:
    """Claim the next diallable queue item, within the client's
    allocation AND the client's wallet (reserve-then-settle: a claim is
    a hold of rate x max_call_minutes; the wallet must cover every hold
    already in flight plus this one).

    Runs inside the caller's (worker_session) transaction. Returns None
    when the allocation is full, the wallet cannot cover another hold,
    or the queue is empty. The caller places the call AFTER this
    transaction commits, using the claim's idempotency key (SD-27).
    """
    from becca.services import wallet

    # Serialise claims per client; the counts and the balance check
    # below are only safe behind this lock (it is the same row lock the
    # wallet's cache updates take, so no check can see a half-applied
    # settle).
    locked = (
        await session.execute(
            text(
                "SELECT channel_allocation, wallet_balance_usd, rate_per_min_usd"
                " FROM client_account WHERE id = :cid FOR UPDATE"
            ),
            {"cid": str(client_account_id)},
        )
    ).first()
    if locked is None:
        return None
    allocation = int(locked[0])

    in_flight = (
        await session.execute(
            text(
                "SELECT count(*) FROM call WHERE client_account_id = :cid AND status = 'dialling'"
            ),
            {"cid": str(client_account_id)},
        )
    ).scalar_one()
    if int(in_flight) >= allocation:
        return None

    # The wallet gate. reserved() prices every in-flight attempt (run
    # calls AND test calls) at its own snapshotted rate; the new hold is
    # at the current rate. Insufficient cover reads exactly like a full
    # allocation — None — and the dispatcher's pre-check is what pauses
    # and notifies.
    held = await wallet.reserved(
        session, client_account_id=client_account_id, max_call_minutes=max_call_minutes
    )
    rate = locked[2]
    new_hold = wallet.per_call_reserve(rate, max_call_minutes)
    if locked[1] - held - new_hold < 0:
        return None

    claimed = (
        await session.execute(
            text(
                """
                UPDATE queue_item
                   SET state = 'dialling',
                       attempts = attempts + 1,
                       last_attempt_at = now()
                 WHERE id = (
                       SELECT id FROM queue_item
                        WHERE agent_id = :aid
                          AND state = 'pending'
                          AND next_attempt_at <= now()
                        ORDER BY next_attempt_at
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1)
             RETURNING id, contact_id, idempotency_key, attempts
                """
            ),
            {"aid": str(agent_id)},
        )
    ).first()
    if claimed is None:
        return None
    queue_item_id, contact_id, queue_key, attempts = claimed

    # An attempt is a call: a retried item produces a second call row,
    # so the call's key is per-attempt (still UNIQUE, SD-27) and the
    # queue join rides the FK, not the key. The rate is snapshotted
    # here so a mid-run rate change never touches calls already claimed
    # — settlement reads the snapshot, not the live rate.
    attempt_key = f"{queue_key}-a{int(attempts)}"
    call_id = (
        await session.execute(
            text(
                """
                INSERT INTO call (agent_id, client_account_id, contact_id,
                                  queue_item_id, status, idempotency_key,
                                  rate_per_min_usd)
                VALUES (:aid, :cid, :contact, :qid, 'dialling', :key, :rate)
             RETURNING id
                """
            ),
            {
                "aid": str(agent_id),
                "cid": str(client_account_id),
                "contact": str(contact_id) if contact_id else None,
                "qid": str(queue_item_id),
                "key": attempt_key,
                "rate": rate,
            },
        )
    ).scalar_one()

    return Claim(
        queue_item_id=queue_item_id,
        call_id=call_id,
        contact_id=contact_id,
        idempotency_key=attempt_key,
    )
