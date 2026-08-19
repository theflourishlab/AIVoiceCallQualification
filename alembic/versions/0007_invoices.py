"""Phase 7: invoices (FRD §14, FR-BILL-1/5/7).

Deviations from §14's column list, recorded here:

- `period` is text 'YYYY-MM' with UNIQUE (client_account_id, period) —
  generation is idempotent per client per month.
- `number_mrc` is stored alongside `telnyx_cost` because it comes from a
  different source (the monthly invoice's $35/number MRC, live-verified
  §11a — it never appears in detail records) and the client-facing view
  must never expose the split (FR-BILL-6); the console must.
- created_at/sent_at/paid_at timestamps carry the manual lifecycle
  (FR-BILL-7: delivered as PDF, paid by bank transfer, marked manually).

The PDF is stored as bytea at generation time — reissue serves the same
bytes (the plan's "byte-identical reissue"). Renderer is fpdf2, not
WeasyPrint (recorded deviation: WeasyPrint needs GTK native libraries,
unavailable on the Windows dev machine; the byte-identity guarantee
lives in the bytea storage, not the renderer).

RLS is tenant-shaped: the client plane reads its own invoices
(FR-BILL-6), the console spans all.
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

STATEMENTS = [
    """
    CREATE TABLE invoice (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        client_account_id uuid NOT NULL REFERENCES client_account(id),
        period text NOT NULL,
        telnyx_cost numeric(10, 4) NOT NULL,
        number_mrc numeric(10, 2) NOT NULL DEFAULT 0,
        margin_pct numeric(5, 2) NOT NULL,
        amount numeric(10, 2) NOT NULL,
        status text NOT NULL DEFAULT 'draft',
        pdf bytea NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        sent_at timestamptz,
        paid_at timestamptz,
        UNIQUE (client_account_id, period),
        CHECK (status IN ('draft', 'sent', 'paid', 'overdue'))
    )
    """,
    "ALTER TABLE invoice ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE invoice FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY tenant_isolation ON invoice
        USING (
            current_setting('app.plane', true) IN ('console', 'worker')
            OR client_account_id = nullif(current_setting('app.client_id', true), '')::uuid
        )
    """,
]


def upgrade() -> None:
    for stmt in STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP TABLE invoice")
