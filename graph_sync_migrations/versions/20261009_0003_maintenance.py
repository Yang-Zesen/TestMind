"""Allow fenced maintenance tasks in the projection queue."""

from alembic import op

revision = "20261009_maintenance"
down_revision = "20261009_generations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE graph_sync.projection_outbox ALTER COLUMN entity_key DROP NOT NULL")
    op.execute("ALTER TABLE graph_sync.projection_outbox ADD COLUMN operation text NOT NULL DEFAULT 'project'")
    op.execute("CREATE UNIQUE INDEX graph_sync_one_rebuild ON graph_sync.projection_outbox (project_id) WHERE operation='rebuild' AND status IN ('queued','processing','failed_retryable')")


def downgrade() -> None:
    op.execute("DROP INDEX graph_sync.graph_sync_one_rebuild")
    op.execute("DELETE FROM graph_sync.projection_outbox WHERE operation<>'project'")
    op.execute("ALTER TABLE graph_sync.projection_outbox DROP COLUMN operation")
    op.execute("ALTER TABLE graph_sync.projection_outbox ALTER COLUMN entity_key SET NOT NULL")
