"""The client wallet view: balance, the rate stated plainly, every
ledger line, receipts from the pre-wallet era — and still never Telnyx
cost or margin. (Minutes are shown now, deliberately: a public flat
rate means they no longer derive anything secret.)"""

import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import text

from becca.db.session import SessionFactory
from tests.web.conftest import STAFF_EMAIL, csrf_from, fund_wallet, sign_in
from tests.web.test_console_flow import _create_client_account

OWNER = "engineer@becca.live"

PDF = b"%PDF-1.4 receipt"


async def _seed_receipt(
    db: SessionFactory, *, status: str = "sent", period: str = "2026-07"
) -> uuid.UUID:
    async with db.console_session() as s:
        client_id = (
            await s.execute(text("SELECT id FROM client_account WHERE name = 'Sylvastar'"))
        ).scalar_one()
        invoice_id = (
            await s.execute(
                text(
                    "INSERT INTO invoice (client_account_id, period, telnyx_cost,"
                    " number_mrc, margin_pct, amount, status, pdf)"
                    " VALUES (:cid, :period, 2.0, 0, 50, 3.00, :st, :pdf) RETURNING id"
                ),
                {"cid": str(client_id), "period": period, "st": status, "pdf": PDF},
            )
        ).scalar_one()
    return uuid.UUID(str(invoice_id))


async def test_wallet_shows_balance_rate_ledger_and_no_secrets(
    console: TestClient, client_plane: TestClient, db: SessionFactory
) -> None:
    sign_in(console, STAFF_EMAIL)
    _create_client_account(console)
    fund_wallet(console, amount=100.0)

    sign_in(client_plane, OWNER)
    page = client_plane.get("/wallet")
    assert page.status_code == 200
    assert "$100.00" in page.text  # the balance KPI
    assert "$0.30 per minute" in page.text  # the rate, stated plainly
    assert "per second" in page.text
    assert "Top-up" in page.text  # the credit is a ledger line
    assert "costs you nothing" in page.text  # no number fee, said out loud
    # Still never: Telnyx, margin, or Becca's cost. Strip markup first —
    # CSS uses "margin" innocently; the CONTENT must not.
    content = re.sub(r"<[^>]*>", " ", page.text).lower()
    assert "telnyx" not in content
    assert "margin" not in content


async def test_old_billing_url_redirects_to_wallet(
    console: TestClient, client_plane: TestClient, db: SessionFactory
) -> None:
    sign_in(console, STAFF_EMAIL)
    _create_client_account(console)
    sign_in(client_plane, OWNER)
    r = client_plane.get("/billing", follow_redirects=False)
    assert r.status_code == 308
    assert r.headers["location"] == "/wallet"


async def test_receipts_visible_and_drafts_invisible(
    console: TestClient, client_plane: TestClient, db: SessionFactory
) -> None:
    sign_in(console, STAFF_EMAIL)
    _create_client_account(console)
    sent_id = await _seed_receipt(db, status="sent")
    draft_id = await _seed_receipt(db, status="draft", period="2026-06")

    sign_in(client_plane, OWNER)
    page = client_plane.get("/wallet")
    assert "Past invoices" in page.text
    assert "2026-07" in page.text
    pdf = client_plane.get(f"/billing/invoices/{sent_id}/pdf")
    assert pdf.status_code == 200
    assert pdf.content == PDF
    # Drafts stay Becca-internal, exactly as before the wallet.
    assert client_plane.get(f"/billing/invoices/{draft_id}/pdf").status_code == 404


async def test_another_clients_receipt_is_not_found(
    console: TestClient, client_plane: TestClient, db: SessionFactory
) -> None:
    sign_in(console, STAFF_EMAIL)
    _create_client_account(console, name="Sylvastar")
    invoice_id = await _seed_receipt(db, status="sent")
    page = console.get("/clients/new")
    console.post(
        "/clients",
        data={
            "csrf_token": csrf_from(page.text),
            "name": "Lekki Gardens",
            "rate_per_min": "0.30",
            "owner_email": "lekki-owner@becca.live",
        },
        follow_redirects=False,
    )
    sign_in(client_plane, "lekki-owner@becca.live")
    assert client_plane.get(f"/billing/invoices/{invoice_id}/pdf").status_code == 404
    page = client_plane.get("/wallet")
    assert "Past invoices" not in page.text  # RLS: nothing of Sylvastar's
    assert "No activity yet" in page.text
