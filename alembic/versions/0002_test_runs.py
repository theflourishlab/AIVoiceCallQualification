"""Phase 2: test runs and the scratch insight map.

test_run stores a snapshot of the schema each test used (FR-TEST-4) —
insight results carry no template revision, so only our record can say
which schema produced which result. telnyx_call_sid is stored because
the dial response carries only that (spike finding); the conversation is
found via the scratch assistant instead.

agent.telnyx_scratch_insight_map maps our field ids to the scratch
assistant's Telnyx insight ids so schema edits can mutate the insight
group in place (FR-TEST-3): derived-artifact bookkeeping, rebuildable.
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

STATEMENTS = [
    """
    CREATE TABLE test_run (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        agent_id uuid NOT NULL REFERENCES agent(id),
        client_account_id uuid NOT NULL REFERENCES client_account(id),
        agent_version_id uuid NOT NULL REFERENCES agent_version(id),
        n integer NOT NULL,
        schema_snapshot jsonb NOT NULL,
        stand_ins jsonb NOT NULL DEFAULT '{}',
        to_number text NOT NULL,
        telnyx_call_sid text,
        telnyx_conversation_id text,
        transcript jsonb,
        results jsonb,
        cost numeric(10,4),
        status text NOT NULL DEFAULT 'dialling'
            CHECK (status IN ('dialling', 'complete', 'failed')),
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (agent_id, n)
    )
    """,
    "ALTER TABLE test_run ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE test_run FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY tenant_isolation ON test_run
        USING (
            current_setting('app.plane', true) IN ('console', 'worker')
            OR client_account_id = nullif(current_setting('app.client_id', true), '')::uuid
        )
    """,
    "ALTER TABLE agent ADD COLUMN telnyx_scratch_insight_map jsonb NOT NULL DEFAULT '{}'",
]


def upgrade() -> None:
    for stmt in STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP TABLE test_run CASCADE")
    op.execute("ALTER TABLE agent DROP COLUMN telnyx_scratch_insight_map")
