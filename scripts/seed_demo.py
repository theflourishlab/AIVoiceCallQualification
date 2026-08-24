"""Seed (or refill) the hackathon demo account.

Creates the demo client account and its app_user if absent, then tops
the wallet up to the target balance. Idempotent: re-running mid-demo is
the wallet refill path. Run from an operator machine against the
deployment's EXTERNAL database URL:

    DATABASE_URL=postgresql+asyncpg://... uv run python scripts/seed_demo.py \
        --email demo@becca.live --credit 20

The ledger entry is attributed to the all-zeros staff sentinel (the
same convention settle_test_call uses), so the append-only ledger and
the audit trail both show the seed for what it is.
"""

import argparse
import asyncio
import re
import uuid
from decimal import Decimal

from sqlalchemy import text

from becca.config import load_settings
from becca.db.session import SessionFactory, make_engine
from becca.services import audit, wallet

SEED_ACTOR = uuid.UUID(int=0)


def _asyncpg_url(url: str) -> str:
    # Render hands out postgres:// or postgresql:// URLs; the app speaks
    # asyncpg only (same rewrite the deployment wizard applies).
    return re.sub(r"^postgres(ql)?://", "postgresql+asyncpg://", url)


async def seed(name: str, email: str, rate: float, credit: float, database_url: str) -> None:
    email = email.strip().lower()
    factory = SessionFactory(make_engine(_asyncpg_url(database_url)))
    try:
        async with factory.console_session() as s:
            client_id = (
                await s.execute(text("SELECT id FROM client_account WHERE name = :n"), {"n": name})
            ).scalar_one_or_none()
            if client_id is None:
                # Mirrors the console's create_client insert; channel
                # allocation stays 0 so launch pre-flight blocks — the
                # demo shows the gates, it never dials a list.
                client_id = (
                    await s.execute(
                        text(
                            "INSERT INTO client_account"
                            " (name, billing_entity, margin_pct, rate_per_min_usd)"
                            " VALUES (:n, :n, 0, :r) RETURNING id"
                        ),
                        {"n": name, "r": round(rate, 2)},
                    )
                ).scalar_one()
                await audit.record(
                    s,
                    actor_type="staff",
                    actor_id=SEED_ACTOR,
                    action="created_client_account",
                    client_account_id=uuid.UUID(str(client_id)),
                    target=name,
                )
                print(f"created client account {client_id} ({name!r})")
            else:
                print(f"client account exists: {client_id} ({name!r})")

            row = (
                await s.execute(
                    text("SELECT id, client_account_id FROM app_user WHERE google_email = :e"),
                    {"e": email},
                )
            ).one_or_none()
            if row is None:
                await s.execute(
                    text(
                        "INSERT INTO app_user (client_account_id, google_email, role)"
                        " VALUES (:cid, :e, 'owner')"
                    ),
                    {"cid": str(client_id), "e": email},
                )
                print(f"created app_user {email!r} (owner)")
            elif uuid.UUID(str(row.client_account_id)) != uuid.UUID(str(client_id)):
                # google_email is globally unique; refusing beats silently
                # signing the demo into somebody else's account.
                raise SystemExit(
                    f"{email!r} already belongs to another client account"
                    f" ({row.client_account_id}) — pick a different --email"
                )
            else:
                print(f"app_user exists: {email!r}")

            balance = (
                await s.execute(
                    text("SELECT wallet_balance_usd FROM client_account WHERE id = :cid"),
                    {"cid": str(client_id)},
                )
            ).scalar_one()
            top_up = Decimal(str(round(credit, 2))) - Decimal(balance)
            if top_up > 0:
                await wallet.credit(
                    s,
                    client_account_id=uuid.UUID(str(client_id)),
                    amount_usd=top_up,
                    staff_id=SEED_ACTOR,
                    note="demo seed top-up",
                )
                await audit.record(
                    s,
                    actor_type="staff",
                    actor_id=SEED_ACTOR,
                    action="credited_wallet",
                    client_account_id=uuid.UUID(str(client_id)),
                    target=str(client_id),
                    meta={"amount": float(top_up), "note": "demo seed top-up"},
                )
                print(f"credited ${top_up} (balance was ${balance})")
            else:
                print(f"wallet already at ${balance}, no top-up")

        print(
            f"\ndemo ready: sign in at https://<client-host>/auth/demo?code=<DEMO_ACCESS_CODE>"
            f"\n  DEMO_USER_EMAIL={email}"
        )
    finally:
        await factory.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="Becca Demo")
    parser.add_argument("--email", required=True, help="google_email of the demo app_user")
    parser.add_argument("--rate", type=float, default=0.30, help="per-minute rate at creation")
    parser.add_argument("--credit", type=float, default=20.0, help="target wallet balance, USD")
    parser.add_argument(
        "--database-url",
        default=None,
        help="defaults to DATABASE_URL from the environment / .env",
    )
    args = parser.parse_args()
    url = args.database_url or load_settings().database_url
    asyncio.run(seed(args.name, args.email, args.rate, args.credit, url))


if __name__ == "__main__":
    main()
