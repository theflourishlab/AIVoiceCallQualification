"""Initial schema: spine tables, FORCE RLS, governor indexes.

Scope per the build plan: everything the spine and the governor's shape
depend on. RLS is structural from migration one (SD-10) and fail-closed:
a session that sets no GUC matches no policy branch and sees zero rows.

The connecting role is demoted from superuser if it is one (the local
docker user), because superusers bypass RLS unconditionally. This makes
dev match Render's shape: table owner, not superuser, subject to FORCE
ROW LEVEL SECURITY.
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TENANT_TABLES = [
    "app_user",
    "agent",
    "agent_version",
    "queue_item",
    "call",
]

SCHEMA = """
CREATE TABLE becca_staff (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    google_email text NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE client_account (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL,
    billing_entity text NOT NULL,
    margin_pct numeric(5,2) NOT NULL,
    channel_allocation integer NOT NULL DEFAULT 0,
    recording_enabled boolean NOT NULL DEFAULT false,
    telnyx_billing_group_id text,
    status text NOT NULL DEFAULT 'onboarding',
    acknowledged_at timestamptz,
    acknowledged_by_user_id uuid,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE app_user (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_account_id uuid NOT NULL REFERENCES client_account(id),
    google_email text NOT NULL UNIQUE,
    role text NOT NULL CHECK (role IN ('owner', 'member')),
    last_seen_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE agent (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_account_id uuid NOT NULL REFERENCES client_account(id),
    name text NOT NULL,
    status text NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft','tested','scheduled','dialling','finished','halted')),
    current_version_id uuid,
    telnyx_scratch_assistant_id text,
    telnyx_scratch_texml_app_id text,
    telnyx_run_assistant_id text,
    telnyx_run_texml_app_id text,
    telnyx_insight_group_id text,
    duplicated_from_agent_id uuid,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE agent_version (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id uuid NOT NULL REFERENCES agent(id),
    client_account_id uuid NOT NULL REFERENCES client_account(id),
    n integer NOT NULL,
    -- The one artifact (FR-AGENT-2). No contract/schema columns: both
    -- are views over fields[].
    fields jsonb NOT NULL,
    script_blocks jsonb NOT NULL,
    voice_settings jsonb NOT NULL DEFAULT '{}',
    telephony_settings jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (agent_id, n)
);

CREATE TABLE queue_item (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id uuid NOT NULL REFERENCES agent(id),
    client_account_id uuid NOT NULL REFERENCES client_account(id),
    contact_id uuid,
    state text NOT NULL DEFAULT 'pending'
        CHECK (state IN ('pending','dialling','done','failed','skipped')),
    attempts integer NOT NULL DEFAULT 0,
    last_attempt_at timestamptz,
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    idempotency_key text NOT NULL UNIQUE
);

CREATE TABLE call (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id uuid NOT NULL REFERENCES agent(id),
    client_account_id uuid NOT NULL REFERENCES client_account(id),
    contact_id uuid,
    telnyx_call_session_id text UNIQUE,
    telnyx_call_control_id text,
    telnyx_conversation_id text,
    telnyx_recording_id text,
    answered_by text,
    status text NOT NULL DEFAULT 'dialling',
    duration_sec integer,
    cost_actual numeric(10,4),
    started_at timestamptz,
    ended_at timestamptz,
    idempotency_key text NOT NULL UNIQUE
);

CREATE TABLE audit_log (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    actor_type text NOT NULL CHECK (actor_type IN ('staff','user')),
    actor_id uuid NOT NULL,
    client_account_id uuid,
    action text NOT NULL,
    target text,
    metadata jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE webhook_event (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    telnyx_event_id text NOT NULL UNIQUE,
    event_type text NOT NULL,
    payload jsonb NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz
);

-- The governor IS these indexes (FR-DISPATCH-9): the in-flight count per
-- client inside the claiming transaction, and the claim path itself.
CREATE INDEX call_dialling_per_client ON call (client_account_id) WHERE status = 'dialling';
CREATE INDEX queue_claimable ON queue_item (agent_id, next_attempt_at) WHERE state = 'pending';
CREATE INDEX agent_by_client ON agent (client_account_id);
CREATE INDEX audit_by_client ON audit_log (client_account_id, created_at);
"""

RLS_PLANE_ONLY = """
ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {t} FORCE ROW LEVEL SECURITY;
CREATE POLICY plane_only ON {t}
    USING (current_setting('app.plane', true) IN ('console', 'worker'));
"""

RLS_TENANT = """
ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {t} FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON {t}
    USING (
        current_setting('app.plane', true) IN ('console', 'worker')
        OR client_account_id = nullif(current_setting('app.client_id', true), '')::uuid
    );
"""

# client_account's own id IS the tenant key.
RLS_CLIENT_ACCOUNT = """
ALTER TABLE client_account ENABLE ROW LEVEL SECURITY;
ALTER TABLE client_account FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON client_account
    USING (
        current_setting('app.plane', true) IN ('console', 'worker')
        OR id = nullif(current_setting('app.client_id', true), '')::uuid
    );
"""

# audit_log: clients may read their own account's audit rows; writes come
# from any authenticated context (the policy's WITH CHECK mirrors USING).
RLS_AUDIT = """
ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON audit_log
    USING (
        current_setting('app.plane', true) IN ('console', 'worker')
        OR client_account_id = nullif(current_setting('app.client_id', true), '')::uuid
    );
"""

# Superusers bypass RLS unconditionally, so migrations (and the app)
# must never run as one — otherwise every RLS test silently passes
# without testing anything. Fail loudly instead.
REFUSE_SUPERUSER = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles
               WHERE rolname = current_user AND (rolsuper OR rolbypassrls)) THEN
        RAISE EXCEPTION 'migrations must run as a non-superuser role '
            '(superusers bypass RLS); connect as the app role instead';
    END IF;
END $$;
"""

APPEND_ONLY_AUDIT = """
REVOKE UPDATE, DELETE ON audit_log FROM PUBLIC;
CREATE RULE audit_no_update AS ON UPDATE TO audit_log DO INSTEAD NOTHING;
CREATE RULE audit_no_delete AS ON DELETE TO audit_log DO INSTEAD NOTHING;
"""


def _statements(sql: str) -> list[str]:
    """Split multi-statement SQL for asyncpg, which refuses compound
    prepared statements. Safe here: no $$-quoted bodies contain ';' at
    statement level except the DO block, which is passed whole."""
    return [s.strip() for s in sql.split(";\n") if s.strip()]


def upgrade() -> None:
    op.execute(REFUSE_SUPERUSER)
    for block in [
        SCHEMA,
        RLS_CLIENT_ACCOUNT,
        *[RLS_TENANT.format(t=t) for t in TENANT_TABLES],
        RLS_PLANE_ONLY.format(t="becca_staff"),
        RLS_PLANE_ONLY.format(t="webhook_event"),
        RLS_AUDIT,
        APPEND_ONLY_AUDIT,
    ]:
        for stmt in _statements(block):
            op.execute(stmt)


def downgrade() -> None:
    for t in [
        "webhook_event",
        "audit_log",
        "call",
        "queue_item",
        "agent_version",
        "agent",
        "app_user",
        "client_account",
        "becca_staff",
    ]:
        op.execute(f"DROP TABLE {t} CASCADE")
