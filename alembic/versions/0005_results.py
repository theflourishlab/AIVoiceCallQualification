"""Phase 5: results become ours (FRD §14, FR-RESULT-1..8).

transcript and insight_result mirror what Telnyx holds, making our
database the system of record — /ai/conversations has no documented
retention window. insight_result stores the field ID alongside the key
(FR-DATA-5) so results stay attributable after a rename, and
corrections live BESIDE originals (FR-RESULT-7), never over them.

Deviations recorded: every table carries client_account_id (SD-10, RLS
without joins) and agent_id (the results table queries per agent);
saved_view is not in §14 but FR-RESULT-3's "views may be saved" needs
storage — "Qualified" is a saved view, not a category.
agent.telnyx_run_insight_map mirrors the scratch map: launch snapshots
field_id -> insight_id so results can be attributed (derived
bookkeeping, rebuildable from Telnyx).
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

STATEMENTS = [
    """
    CREATE TABLE transcript (
        call_id uuid PRIMARY KEY REFERENCES call(id),
        client_account_id uuid NOT NULL REFERENCES client_account(id),
        agent_id uuid NOT NULL REFERENCES agent(id),
        messages jsonb NOT NULL DEFAULT '[]',
        fetched_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "ALTER TABLE transcript ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE transcript FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY tenant_isolation ON transcript
        USING (
            current_setting('app.plane', true) IN ('console', 'worker')
            OR client_account_id = nullif(current_setting('app.client_id', true), '')::uuid
        )
    """,
    """
    CREATE TABLE insight_result (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        call_id uuid NOT NULL REFERENCES call(id),
        client_account_id uuid NOT NULL REFERENCES client_account(id),
        agent_id uuid NOT NULL REFERENCES agent(id),
        field_id integer NOT NULL,
        field_key text NOT NULL,
        value text,
        corrected_value text,
        corrected_by uuid,
        corrected_at timestamptz,
        source_quote text,
        source_timestamp_sec numeric(10,2),
        UNIQUE (call_id, field_id)
    )
    """,
    "ALTER TABLE insight_result ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE insight_result FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY tenant_isolation ON insight_result
        USING (
            current_setting('app.plane', true) IN ('console', 'worker')
            OR client_account_id = nullif(current_setting('app.client_id', true), '')::uuid
        )
    """,
    "CREATE INDEX insight_result_agent_idx ON insight_result (agent_id, field_id)",
    """
    CREATE TABLE saved_view (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        agent_id uuid NOT NULL REFERENCES agent(id),
        client_account_id uuid NOT NULL REFERENCES client_account(id),
        name text NOT NULL,
        filters jsonb NOT NULL DEFAULT '{}',
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (agent_id, name)
    )
    """,
    "ALTER TABLE saved_view ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE saved_view FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY tenant_isolation ON saved_view
        USING (
            current_setting('app.plane', true) IN ('console', 'worker')
            OR client_account_id = nullif(current_setting('app.client_id', true), '')::uuid
        )
    """,
    "ALTER TABLE agent ADD COLUMN telnyx_run_insight_map jsonb NOT NULL DEFAULT '{}'",
]


def upgrade() -> None:
    for stmt in STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP TABLE saved_view CASCADE")
    op.execute("DROP TABLE insight_result CASCADE")
    op.execute("DROP TABLE transcript CASCADE")
    op.execute("ALTER TABLE agent DROP COLUMN telnyx_run_insight_map")
