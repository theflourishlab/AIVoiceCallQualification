"""Prepaid wallet + flat per-minute billing (14 Aug 2026 direction).

Supersedes the post-paid invoice model (FR-BILL-1/5/7): clients fund a
wallet first, calls debit it at a flat per-client per-minute rate, and
FR-BILL-8 is deliberately inverted — insufficient balance DOES pause a
run. Existing invoices are kept readable as receipts; nothing here
touches the invoice table.

Decisions recorded:

- `wallet_ledger` is append-only (audit_log's rule set) and is the
  source of truth; `client_account.wallet_balance_usd` is a cache
  updated in the same transaction as every ledger insert, never
  written otherwise.
- Ledger rows key on `call.id` / `test_run.id` via partial UNIQUE
  indexes, NOT on `call.idempotency_key` — the dispatcher's 403 refund
  path deletes a dialling call row and reuses its key, while a uuid id
  is never reused. The FK on call_id doubles as a tripwire: a settled
  call can never be deleted.
- `rate_per_min_usd` is snapshotted onto `call` / `test_run` when the
  attempt is claimed, and settlement reads the snapshot: no call ever
  bills at a rate that was not in force when it dialled.
- `client_account.margin_pct` is retained untouched: old invoices'
  math references it. New code never reads it.
- Becca absorbs number MRC ($35/no./mo) by decision — there is no MRC
  entry type, and that absence is deliberate, not an oversight.
- Amounts are numeric(10,2): the wallet deals in billed money (2 dp
  convention), never metered cost (4 dp).
- Append-only is enforced by a RAISING TRIGGER, not audit_log's silent
  DO INSTEAD NOTHING rules — Postgres refuses ON CONFLICT on a table
  with rules (live-hit 14 Aug 2026), and settlement's idempotency IS
  an ON CONFLICT. A raise is also the right posture for money: a
  mutation attempt on the ledger is a bug and must be heard, not
  swallowed. TRUNCATE still works (row triggers don't fire), which the
  test suite's cleanup relies on.
"""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

STATEMENTS = [
    """
    ALTER TABLE client_account
        ADD COLUMN rate_per_min_usd numeric(10, 2) NOT NULL DEFAULT 0.30
    """,
    """
    ALTER TABLE client_account
        ADD COLUMN wallet_balance_usd numeric(10, 2) NOT NULL DEFAULT 0
    """,
    "ALTER TABLE call ADD COLUMN rate_per_min_usd numeric(10, 2)",
    "ALTER TABLE test_run ADD COLUMN rate_per_min_usd numeric(10, 2)",
    "ALTER TABLE test_run ADD COLUMN duration_sec integer",
    """
    CREATE TABLE wallet_ledger (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        client_account_id uuid NOT NULL REFERENCES client_account(id),
        entry_type text NOT NULL,
        amount_usd numeric(10, 2) NOT NULL,
        call_id uuid REFERENCES call(id),
        test_run_id uuid REFERENCES test_run(id),
        duration_sec integer,
        billed_minutes integer,
        rate_per_min_usd numeric(10, 2),
        idempotency_key text,
        note text NOT NULL DEFAULT '',
        created_by uuid,
        created_at timestamptz NOT NULL DEFAULT now(),
        CHECK (entry_type IN ('credit', 'debit_call', 'debit_test_call', 'adjustment')),
        CHECK (amount_usd <> 0),
        CHECK (
            (entry_type = 'credit' AND amount_usd > 0)
            OR (entry_type IN ('debit_call', 'debit_test_call') AND amount_usd < 0)
            OR entry_type = 'adjustment'
        )
    )
    """,
    """
    CREATE UNIQUE INDEX wallet_ledger_call_once
        ON wallet_ledger (call_id) WHERE call_id IS NOT NULL
    """,
    """
    CREATE UNIQUE INDEX wallet_ledger_test_run_once
        ON wallet_ledger (test_run_id) WHERE test_run_id IS NOT NULL
    """,
    """
    CREATE INDEX wallet_ledger_client_created
        ON wallet_ledger (client_account_id, created_at DESC)
    """,
    "ALTER TABLE wallet_ledger ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE wallet_ledger FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY tenant_isolation ON wallet_ledger
        USING (
            current_setting('app.plane', true) IN ('console', 'worker')
            OR client_account_id = nullif(current_setting('app.client_id', true), '')::uuid
        )
    """,
    "REVOKE UPDATE, DELETE ON wallet_ledger FROM PUBLIC",
    """
    CREATE FUNCTION wallet_ledger_immutable() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        RAISE EXCEPTION 'wallet_ledger is append-only';
    END $$
    """,
    """
    CREATE TRIGGER wallet_ledger_append_only
        BEFORE UPDATE OR DELETE ON wallet_ledger
        FOR EACH ROW EXECUTE FUNCTION wallet_ledger_immutable()
    """,
]


def upgrade() -> None:
    for stmt in STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP TABLE wallet_ledger")
    op.execute("DROP FUNCTION IF EXISTS wallet_ledger_immutable()")
    op.execute("ALTER TABLE test_run DROP COLUMN duration_sec")
    op.execute("ALTER TABLE test_run DROP COLUMN rate_per_min_usd")
    op.execute("ALTER TABLE call DROP COLUMN rate_per_min_usd")
    op.execute("ALTER TABLE client_account DROP COLUMN wallet_balance_usd")
    op.execute("ALTER TABLE client_account DROP COLUMN rate_per_min_usd")
