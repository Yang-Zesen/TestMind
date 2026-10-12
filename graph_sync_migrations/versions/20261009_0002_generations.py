"""Track the online graph generation per project."""

from alembic import op

revision = "20261009_generations"
down_revision = "20261009_graph_sync"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE graph_sync.project_generations (
        project_id text PRIMARY KEY,generation text NOT NULL,
        updated_at timestamptz NOT NULL DEFAULT now())""")


def downgrade() -> None:
    op.execute("DROP TABLE graph_sync.project_generations")
