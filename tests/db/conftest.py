import uuid

from sqlalchemy import text

from becca.db.session import SessionFactory


async def seed_client(
    factory: SessionFactory,
    *,
    name: str,
    allocation: int = 1,
    rate_per_min: float = 0.30,
    balance: float = 1000.0,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Create a client account with one agent; return (client_id, agent_id).

    The wallet defaults are deliberately generous so tests that are not
    about money never trip the reserve gate. balance > 0 seeds a credit
    ledger row so cache == ledger holds from the start."""
    async with factory.console_session() as s:
        client_id = (
            await s.execute(
                text(
                    "INSERT INTO client_account (name, billing_entity, margin_pct,"
                    " channel_allocation, rate_per_min_usd, wallet_balance_usd)"
                    " VALUES (:n, :n, 50, :alloc, :rate, :bal) RETURNING id"
                ),
                {"n": name, "alloc": allocation, "rate": rate_per_min, "bal": balance},
            )
        ).scalar_one()
        if balance:
            await s.execute(
                text(
                    "INSERT INTO wallet_ledger (client_account_id, entry_type,"
                    " amount_usd, note, created_by)"
                    " VALUES (:cid, 'credit', :bal, 'test seed',"
                    " '00000000-0000-0000-0000-000000000000')"
                ),
                {"cid": str(client_id), "bal": balance},
            )
        agent_id = (
            await s.execute(
                text("INSERT INTO agent (client_account_id, name) VALUES (:cid, :n) RETURNING id"),
                {"cid": str(client_id), "n": f"{name}-agent"},
            )
        ).scalar_one()
    return client_id, agent_id
