"""Phase 6: the console's own state (FR-CONSOLE-3..7).

Deviations from FRD §14, recorded here:

- phone_number is not in §14 at all, but FR-CONSOLE-1/4/5 need somewhere
  to hold "this number belongs to that client" — Telnyx has no native
  per-client assignment until billing groups arrive (Phase 7), and the
  launch pre-flight ("no number assigned" is blocking) plus the
  dispatcher's caller ID both read it. Inventory facts (status, country)
  sync from Telnyx when the console renders; the assignment is ours.
- balance_snapshot exists so "projected exhaustion date" (FR-CONSOLE-6,
  FR-NOTIFY-2A) can be computed from observed burn, since call.cost_actual
  stays empty until Phase 7 reads detail records. One row per console
  landing render, throttled in code to at most one per hour.
- telnyx_account_note is the single-row home for the health facts Telnyx
  exposes in Mission Control but not in the API (verification tier,
  regulatory document expiry). Staff-recorded, provenance shown in the UI
  (FR-NOTIFY-2B: the v1 backstop is human).

RLS: phone_number is tenant-shaped — a client session may see its own
assigned numbers (the pre-flight runs in one); unassigned numbers have a
NULL client_account_id and are console/worker business only. The other
two tables are plane-only.
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

STATEMENTS = [
    """
    CREATE TABLE phone_number (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        telnyx_phone_number_id text UNIQUE,
        phone_e164 text NOT NULL UNIQUE,
        country text NOT NULL DEFAULT 'NG',
        client_account_id uuid REFERENCES client_account(id),
        number_use text NOT NULL DEFAULT 'outbound',
        status text NOT NULL DEFAULT 'active',
        assigned_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "ALTER TABLE phone_number ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE phone_number FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY tenant_isolation ON phone_number
        USING (
            current_setting('app.plane', true) IN ('console', 'worker')
            OR client_account_id = nullif(current_setting('app.client_id', true), '')::uuid
        )
    """,
    """
    CREATE TABLE balance_snapshot (
        id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        available_credit numeric(12, 4) NOT NULL,
        currency text NOT NULL DEFAULT 'USD',
        taken_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "ALTER TABLE balance_snapshot ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE balance_snapshot FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY plane_only ON balance_snapshot
        USING (current_setting('app.plane', true) IN ('console', 'worker'))
    """,
    """
    CREATE TABLE telnyx_account_note (
        id boolean PRIMARY KEY DEFAULT true CHECK (id),
        verification_tier text,
        doc_expiry date,
        noted_by_staff_id uuid REFERENCES becca_staff(id),
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "ALTER TABLE telnyx_account_note ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE telnyx_account_note FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY plane_only ON telnyx_account_note
        USING (current_setting('app.plane', true) IN ('console', 'worker'))
    """,
    "CREATE INDEX balance_snapshot_by_time ON balance_snapshot (taken_at)",
    "CREATE INDEX phone_number_by_client ON phone_number (client_account_id)",
]


def upgrade() -> None:
    for stmt in STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP TABLE telnyx_account_note")
    op.execute("DROP TABLE balance_snapshot")
    op.execute("DROP TABLE phone_number")
