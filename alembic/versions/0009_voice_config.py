"""Issue #1 strand 3: the voice & behaviour screen (FR-AGENT-9/10/11).

One jsonb of per-agent overrides on the agent row, empty by default.
Absent keys fall back to deployment configuration (conversation model,
voice) or the FRD defaults (speed 1.0, deepgram/flux, interruption on,
300s cap, 10s idle) — resolution happens in services/voice_config.py,
never in SQL, so the column stays a plain store of what the user chose.
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE agent ADD COLUMN voice_config jsonb NOT NULL DEFAULT '{}'::jsonb")


def downgrade() -> None:
    op.execute("ALTER TABLE agent DROP COLUMN voice_config")
