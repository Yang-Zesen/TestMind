"""Make unsupported capabilities an explicit durable terminal state."""

from alembic import op

revision = "20261014_p5_status"
down_revision = "20261014_cases"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE business.tasks DROP CONSTRAINT IF EXISTS tasks_status_check")
    op.execute("""ALTER TABLE business.tasks ADD CONSTRAINT tasks_status_check CHECK
      (status IN ('queued','running','waiting_input','waiting_review','completed',
      'denied','failed','needs_reconciliation','cancelled','rejected','not_enabled'))""")


def downgrade():
    op.execute("UPDATE business.tasks SET status='failed',error_code='capability_not_enabled' WHERE status='not_enabled'")
    op.execute("ALTER TABLE business.tasks DROP CONSTRAINT IF EXISTS tasks_status_check")
    op.execute("""ALTER TABLE business.tasks ADD CONSTRAINT tasks_status_check CHECK
      (status IN ('queued','running','waiting_input','waiting_review','completed',
      'denied','failed','needs_reconciliation','cancelled','rejected'))""")
