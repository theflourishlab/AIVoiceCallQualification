"""The console side of the wallet: credits, adjustments, rates — every
money move audited, the client told what matters."""

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import text

from becca.db.session import SessionFactory
from tests.web.conftest import STAFF_EMAIL, csrf_from, sign_in
from tests.web.test_console_flow import _create_client_account


async def _client_id(db: SessionFactory) -> uuid.UUID:
    async with db.console_session() as s:
        return uuid.UUID(str((await s.execute(text("SELECT id FROM client_account"))).scalar_one()))


async def test_credit_writes_ledger_audit_and_notification(
    console: TestClient, db: SessionFactory
) -> None:
    sign_in(console, STAFF_EMAIL)
    _create_client_account(console)
    client_id = await _client_id(db)
    csrf = csrf_from(console.get("/billing").text)
    r = console.post(
        f"/billing/credit/{client_id}",
        data={"csrf_token": csrf, "amount_usd": "250.50", "note": "GTB ref 4411"},
        follow_redirects=False,
    )
    assert "done=credited" in r.headers["location"]
    async with db.console_session() as s:
        row = (
            await s.execute(
                text(
                    "SELECT entry_type, amount_usd, note FROM wallet_ledger"
                    " WHERE client_account_id = :cid"
                ),
                {"cid": str(client_id)},
            )
        ).one()
        balance = (
            await s.execute(text("SELECT wallet_balance_usd FROM client_account"))
        ).scalar_one()
        actions = (await s.execute(text("SELECT action FROM audit_log"))).scalars().all()
        notified = (
            await s.execute(
                text("SELECT count(*) FROM notification WHERE event = 'wallet_credited'")
            )
        ).scalar_one()
    assert row[0] == "credit"
    assert float(row[1]) == 250.50
    assert row[2] == "GTB ref 4411"
    assert float(balance) == 250.50
    assert "credited_wallet" in actions
    assert int(notified) == 1
    # The wallet row on the screen shows the new balance.
    assert "$250.50" in console.get("/billing").text


async def test_credit_validation_bounds(console: TestClient, db: SessionFactory) -> None:
    sign_in(console, STAFF_EMAIL)
    _create_client_account(console)
    client_id = await _client_id(db)
    csrf = csrf_from(console.get("/billing").text)
    for bad in ("0", "-5", "100001"):
        r = console.post(
            f"/billing/credit/{client_id}",
            data={"csrf_token": csrf, "amount_usd": bad, "note": ""},
            follow_redirects=False,
        )
        assert "error=amount" in r.headers["location"]
    async with db.console_session() as s:
        count = (await s.execute(text("SELECT count(*) FROM wallet_ledger"))).scalar_one()
    assert int(count) == 0


async def test_adjustment_requires_a_note(console: TestClient, db: SessionFactory) -> None:
    sign_in(console, STAFF_EMAIL)
    _create_client_account(console)
    client_id = await _client_id(db)
    csrf = csrf_from(console.get("/billing").text)
    console.post(
        f"/billing/credit/{client_id}",
        data={"csrf_token": csrf, "amount_usd": "50", "note": "seed"},
        follow_redirects=False,
    )

    r = console.post(
        f"/billing/adjust/{client_id}",
        data={"csrf_token": csrf, "amount_usd": "-20", "note": "  "},
        follow_redirects=False,
    )
    assert "error=note" in r.headers["location"]

    r = console.post(
        f"/billing/adjust/{client_id}",
        data={"csrf_token": csrf, "amount_usd": "-20", "note": "mistyped top-up, was 30"},
        follow_redirects=False,
    )
    assert "done=adjusted" in r.headers["location"]
    async with db.console_session() as s:
        balance = (
            await s.execute(text("SELECT wallet_balance_usd FROM client_account"))
        ).scalar_one()
        actions = (await s.execute(text("SELECT action FROM audit_log"))).scalars().all()
    # 50 - 20 = 30
    assert float(balance) == 30.0
    assert "adjusted_wallet" in actions


async def test_rate_change_notifies_exactly_once(console: TestClient, db: SessionFactory) -> None:
    """Silent repricing is forbidden; so is a notification for a no-op
    save. The audit row and the client notice fire only on change."""
    sign_in(console, STAFF_EMAIL)
    _create_client_account(console)
    client_id = await _client_id(db)
    csrf = csrf_from(console.get("/billing").text)

    r = console.post(
        f"/billing/rate/{client_id}",
        data={"csrf_token": csrf, "rate_per_min": "0.40"},
        follow_redirects=False,
    )
    assert "done=rate" in r.headers["location"]
    # Saving the identical rate again: no second notification, no audit.
    console.post(
        f"/billing/rate/{client_id}",
        data={"csrf_token": csrf, "rate_per_min": "0.40"},
        follow_redirects=False,
    )
    async with db.console_session() as s:
        rate = (await s.execute(text("SELECT rate_per_min_usd FROM client_account"))).scalar_one()
        notices = (
            await s.execute(text("SELECT count(*) FROM notification WHERE event = 'rate_changed'"))
        ).scalar_one()
        audits = (
            await s.execute(
                text("SELECT count(*) FROM audit_log WHERE action = 'set_rate_per_min'")
            )
        ).scalar_one()
    assert float(rate) == 0.40
    assert int(notices) == 1
    assert int(audits) == 1

    r = console.post(
        f"/billing/rate/{client_id}",
        data={"csrf_token": csrf, "rate_per_min": "0.005"},
        follow_redirects=False,
    )
    assert "error=rate" in r.headers["location"]


async def test_ledger_page_shows_entries_and_adjustment_form(
    console: TestClient, db: SessionFactory
) -> None:
    sign_in(console, STAFF_EMAIL)
    _create_client_account(console)
    client_id = await _client_id(db)
    csrf = csrf_from(console.get("/billing").text)
    console.post(
        f"/billing/credit/{client_id}",
        data={"csrf_token": csrf, "amount_usd": "100", "note": "opening balance"},
        follow_redirects=False,
    )
    page = console.get(f"/billing/wallet/{client_id}")
    assert page.status_code == 200
    assert "Top-up" in page.text
    assert "opening balance" in page.text
    assert "/billing/adjust/" in page.text
