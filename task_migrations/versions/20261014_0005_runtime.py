"""Durable component readiness and bounded operator task retries."""

from alembic import op

revision = "20261014_runtime"
down_revision = "20261014_p5_status"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE business.tasks ADD COLUMN manual_retries integer NOT NULL DEFAULT 0")
    op.execute("""CREATE TABLE business.component_heartbeats (
        instance_id text PRIMARY KEY, component text NOT NULL,
        status text NOT NULL, model_ready boolean NOT NULL DEFAULT false,
        started_at timestamptz NOT NULL DEFAULT now(), seen_at timestamptz NOT NULL DEFAULT now())""")
    op.execute("CREATE INDEX component_heartbeats_seen ON business.component_heartbeats(component,seen_at)")


def downgrade():
    op.execute("DROP TABLE business.component_heartbeats")
    op.execute("ALTER TABLE business.tasks DROP COLUMN manual_retries")
