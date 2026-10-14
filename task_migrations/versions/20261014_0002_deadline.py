"""Persist a per-input-version execution deadline across retries and restarts."""

from alembic import op

revision = "20261014_deadline"
down_revision = "20261014_tasks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE business.tasks ADD COLUMN execution_deadline_at timestamptz")


def downgrade() -> None:
    op.execute("ALTER TABLE business.tasks DROP COLUMN execution_deadline_at")
