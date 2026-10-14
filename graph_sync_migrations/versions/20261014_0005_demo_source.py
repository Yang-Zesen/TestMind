"""Independent local graph source event journal and generation retention."""

from alembic import op

revision = "20261014_graph_demo"
down_revision = "20261009_permissions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE graph_sync.demo_source_events (
        sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        event_id text UNIQUE NOT NULL, project_id text NOT NULL,
        resource_type text NOT NULL, payload jsonb NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now())""")
    op.execute("CREATE INDEX graph_demo_stream ON graph_sync.demo_source_events(project_id,resource_type,sequence)")
    op.execute("""CREATE TABLE graph_sync.retired_generations (
        project_id text NOT NULL, generation text NOT NULL,
        retired_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(project_id,generation))""")


def downgrade() -> None:
    op.execute("DROP TABLE graph_sync.retired_generations")
    op.execute("DROP TABLE graph_sync.demo_source_events")
