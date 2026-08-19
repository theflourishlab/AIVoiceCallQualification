"""Legacy invoices survive the wallet cutover as receipts: readable,
markable forward-only, PDFs served byte-identical from storage — but
nothing generates new ones (the generate route is gone)."""

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import text

from becca.db.session import SessionFactory
from tests.web.conftest import STAFF_EMAIL, csrf_from, sign_in
from tests.web.test_console_flow import _create_client_account

PDF = b"%PDF-1.4 legacy receipt bytes, frozen at generation time"


async def _seed_invoice(db: SessionFactory, *, status: str = "draft") -> uuid.UUID:
    """A pre-wallet invoice, inserted directly — the app can no longer
    create one, which is the point."""
    async with db.console_session() as s:
        client_id = (await s.execute(text("SELECT id FROM client_account"))).scalar_one()
        invoice_id = (
            await s.execute(
                text(
                    "INSERT INTO invoice (client_account_id, period, telnyx_cost,"
                    " number_mrc, margin_pct, amount, status, pdf)"
                    " VALUES (:cid, '2026-07', 35.526, 35.0, 50, 53.29, :st, :pdf)"
                    " RETURNING id"
                ),
                {"cid": str(client_id), "st": status, "pdf": PDF},
            )
        ).scalar_one()
    return uuid.UUID(str(invoice_id))


async def test_invoice_generation_is_gone(console: TestClient, db: SessionFactory) -> None:
    sign_in(console, STAFF_EMAIL)
    _create_client_account(console)
    csrf = csrf_from(console.get("/billing").text)
    response = console.post(
        "/billing/generate",
        data={"csrf_token": csrf, "period": "2026-08"},
        follow_redirects=False,
    )
    assert response.status_code == 404  # the route no longer exists
    async with db.console_session() as s:
        count = (await s.execute(text("SELECT count(*) FROM invoice"))).scalar_one()
    assert int(count) == 0


async def test_receipts_render_on_the_console(console: TestClient, db: SessionFactory) -> None:
    sign_in(console, STAFF_EMAIL)
    _create_client_account(console)
    await _seed_invoice(db, status="sent")
    page = console.get("/billing")
    assert page.status_code == 200
    assert "Legacy invoices" in page.text
    assert "$53.29" in page.text
    assert "2026-07" in page.text


async def test_receipt_lifecycle_never_regresses(console: TestClient, db: SessionFactory) -> None:
    sign_in(console, STAFF_EMAIL)
    _create_client_account(console)
    invoice_id = await _seed_invoice(db)
    csrf = csrf_from(console.get("/billing").text)

    for status in ("sent", "paid"):
        r = console.post(
            f"/billing/invoices/{invoice_id}/mark",
            data={"csrf_token": csrf, "status": status},
            follow_redirects=False,
        )
        assert "done=marked" in r.headers["location"]

    # Paid is terminal: marking sent again is refused.
    r = console.post(
        f"/billing/invoices/{invoice_id}/mark",
        data={"csrf_token": csrf, "status": "sent"},
        follow_redirects=False,
    )
    assert "error=transition" in r.headers["location"]
    async with db.console_session() as s:
        row = (await s.execute(text("SELECT status, paid_at FROM invoice"))).one()
    assert row[0] == "paid"
    assert row[1] is not None


async def test_receipt_pdf_is_the_stored_bytes(console: TestClient, db: SessionFactory) -> None:
    sign_in(console, STAFF_EMAIL)
    _create_client_account(console)
    invoice_id = await _seed_invoice(db, status="paid")
    first = console.get(f"/billing/invoices/{invoice_id}/pdf")
    assert first.status_code == 200
    assert first.headers["content-type"] == "application/pdf"
    assert first.content == PDF
    # Reissue is byte-identical because nothing is ever re-rendered.
    assert console.get(f"/billing/invoices/{invoice_id}/pdf").content == first.content
