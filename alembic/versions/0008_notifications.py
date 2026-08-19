"""Phase 8: notifications (FR-NOTIFY-1/3).

In-app only — no email, ever. One row per event; read state is per
reader in notification_read (fan-out at read time, so worker emitters
never enumerate users); preferences are per reader per event, stored
only as overrides (absent row = enabled, FR-NOTIFY-3).

reader_id spans both identity tables (app_user.id / becca_staff.id) —
uuids cannot collide across them, and RLS already separates the planes,
so a discriminator column would record nothing the policies don't.

Audience is the client_account_id column: a client account's rows for
its users, NULL for Becca staff. The tenant policy makes staff rows
structurally invisible to client sessions (NULL never equals their
GUC), and console/worker span everything, per the 0001 pattern.
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

TENANT_POLICY = """
CREATE POLICY tenant_isolation ON {t}
    USING (
        current_setting('app.plane', true) IN ('console', 'worker')
        OR client_account_id = nullif(current_setting('app.client_id', true), '')::uuid
    )
"""

STATEMENTS = [
    """
    CREATE TABLE notification (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        client_account_id uuid REFERENCES client_account(id),
        event text NOT NULL,
        title text NOT NULL,
        body text NOT NULL DEFAULT '',
        agent_id uuid REFERENCES agent(id),
        created_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE notification_read (
        notification_id uuid NOT NULL REFERENCES notification(id) ON DELETE CASCADE,
        client_account_id uuid REFERENCES client_account(id),
        reader_id uuid NOT NULL,
        read_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (notification_id, reader_id)
    )
    """,
    """
    CREATE TABLE notification_pref (
        reader_id uuid NOT NULL,
        client_account_id uuid REFERENCES client_account(id),
        event text NOT NULL,
        enabled boolean NOT NULL,
        PRIMARY KEY (reader_id, event)
    )
    """,
    "ALTER TABLE notification ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE notification FORCE ROW LEVEL SECURITY",
    TENANT_POLICY.format(t="notification"),
    "ALTER TABLE notification_read ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE notification_read FORCE ROW LEVEL SECURITY",
    TENANT_POLICY.format(t="notification_read"),
    "ALTER TABLE notification_pref ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE notification_pref FORCE ROW LEVEL SECURITY",
    TENANT_POLICY.format(t="notification_pref"),
    "CREATE INDEX notification_by_audience ON notification (client_account_id, created_at DESC)",
]


def upgrade() -> None:
    for stmt in STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP TABLE notification_pref")
    op.execute("DROP TABLE notification_read")
    op.execute("DROP TABLE notification")
