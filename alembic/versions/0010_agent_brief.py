"""Store the plain-English description an agent was built from.

v1 used the brief as a one-shot generation seed and discarded it, which
made step 1 of the wizard a door that locked behind you (user feedback,
14 Aug 2026). Persisting it lets the Describe screen reopen pre-launch:
edit the description, rebuild the agent from it. Agents created before
this column carry an empty brief until their owner writes one.
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE agent ADD COLUMN brief text NOT NULL DEFAULT ''")


def downgrade() -> None:
    op.execute("ALTER TABLE agent DROP COLUMN brief")
