"""Phase 3: contact lists and contacts (FRD §14).

column_mapping maps a spreadsheet column name to a field id, never a
field name, so a rename cannot orphan it (FR-DATA-4). The uploaded file
is kept as bytea — the only binary we store besides invoice PDFs
(FR-DATA-6) — so mapping changes recompute rows from the original.

Deviations from §14's column list, recorded here: contact carries
client_account_id (every tenant-scoped table does, so RLS never joins —
SD-10), phone_raw (unparseable numbers are retained and shown for
review, FR-CONTACT-3, and E.164 is null for them), and row_index (the
preview names rows by their position in the file, FR-CONTACT-7).

queue_item.contact_id and call.contact_id gain their promised FKs now
that contact exists (see the 0001-era note in db/tables.py).
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

STATEMENTS = [
    """
    CREATE TABLE contact_list (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        client_account_id uuid NOT NULL REFERENCES client_account(id),
        agent_id uuid NOT NULL REFERENCES agent(id),
        filename text NOT NULL,
        row_count integer NOT NULL DEFAULT 0,
        diallable_count integer NOT NULL DEFAULT 0,
        column_mapping jsonb NOT NULL DEFAULT '{}',
        source_file bytea NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "ALTER TABLE contact_list ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE contact_list FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY tenant_isolation ON contact_list
        USING (
            current_setting('app.plane', true) IN ('console', 'worker')
            OR client_account_id = nullif(current_setting('app.client_id', true), '')::uuid
        )
    """,
    """
    CREATE TABLE contact (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        contact_list_id uuid NOT NULL REFERENCES contact_list(id) ON DELETE CASCADE,
        client_account_id uuid NOT NULL REFERENCES client_account(id),
        row_index integer NOT NULL,
        phone_raw text NOT NULL DEFAULT '',
        phone_e164 text,
        variables jsonb NOT NULL DEFAULT '{}',
        dedupe_key text NOT NULL,
        diallable boolean NOT NULL DEFAULT false,
        exclusion_reason text
            CHECK (exclusion_reason IN ('unparseable_number', 'missing_required_value')),
        UNIQUE (contact_list_id, dedupe_key)
    )
    """,
    "ALTER TABLE contact ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE contact FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY tenant_isolation ON contact
        USING (
            current_setting('app.plane', true) IN ('console', 'worker')
            OR client_account_id = nullif(current_setting('app.client_id', true), '')::uuid
        )
    """,
    "ALTER TABLE queue_item ADD CONSTRAINT queue_item_contact_id_fkey"
    " FOREIGN KEY (contact_id) REFERENCES contact(id)",
    "ALTER TABLE call ADD CONSTRAINT call_contact_id_fkey"
    " FOREIGN KEY (contact_id) REFERENCES contact(id)",
]


def upgrade() -> None:
    for stmt in STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("ALTER TABLE call DROP CONSTRAINT call_contact_id_fkey")
    op.execute("ALTER TABLE queue_item DROP CONSTRAINT queue_item_contact_id_fkey")
    op.execute("DROP TABLE contact CASCADE")
    op.execute("DROP TABLE contact_list CASCADE")
