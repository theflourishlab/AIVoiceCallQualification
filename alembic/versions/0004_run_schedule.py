"""Phase 4: the run schedule (FRD §14).

One row per agent — an agent runs once (duplicate it to run again), so
the run IS the agent and `paused` here is the single authoritative
pause flag (adopted default; agent status stays 'dialling' while
paused, FR-DISPATCH-6).

Deviations from §14's column list, recorded here: client_account_id
(every tenant-scoped table carries it so RLS never joins — SD-10) and
contact_list_id (the launch binds one mapped list to the run; the
server-side pre-flight re-check and the results screens need that
binding without inferring it from queue rows).

call also gains queue_item_id here: a retried queue item produces a
SECOND call row (an attempt is a call), so the call↔queue join cannot
ride the idempotency key — call keys are per-attempt
("<queue-key>-a<n>", still UNIQUE per SD-27), and the FK is the join.
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

STATEMENTS = [
    """
    CREATE TABLE run_schedule (
        agent_id uuid PRIMARY KEY REFERENCES agent(id),
        client_account_id uuid NOT NULL REFERENCES client_account(id),
        contact_list_id uuid NOT NULL REFERENCES contact_list(id),
        window_start time NOT NULL,
        window_end time NOT NULL,
        days integer[] NOT NULL,
        excluded_bands jsonb NOT NULL DEFAULT '[]',
        timezone text NOT NULL DEFAULT 'Africa/Lagos',
        retry_attempts integer NOT NULL DEFAULT 0,
        retry_interval_secs integer NOT NULL DEFAULT 3600,
        spend_cap numeric(10,2) NOT NULL,
        not_before timestamptz,
        paused boolean NOT NULL DEFAULT false,
        created_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "ALTER TABLE run_schedule ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE run_schedule FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY tenant_isolation ON run_schedule
        USING (
            current_setting('app.plane', true) IN ('console', 'worker')
            OR client_account_id = nullif(current_setting('app.client_id', true), '')::uuid
        )
    """,
    "ALTER TABLE call ADD COLUMN queue_item_id uuid REFERENCES queue_item(id)",
]


def upgrade() -> None:
    for stmt in STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("ALTER TABLE call DROP COLUMN queue_item_id")
    op.execute("DROP TABLE run_schedule CASCADE")
