"""Bounded minute-bucket runtime measurements without request content."""

from alembic import op

revision = "20261014_metrics"
down_revision = "20261014_runtime"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE business.runtime_metrics (
        component text NOT NULL, metric text NOT NULL, window_start timestamptz NOT NULL,
        observations integer NOT NULL DEFAULT 0, failures integer NOT NULL DEFAULT 0,
        total_seconds double precision NOT NULL DEFAULT 0, max_seconds double precision NOT NULL DEFAULT 0,
        PRIMARY KEY(component,metric,window_start))""")


def downgrade():
    op.execute("DROP TABLE business.runtime_metrics")
