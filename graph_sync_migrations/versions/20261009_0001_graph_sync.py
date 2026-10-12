"""Graph authority, receipts, leased projection outbox and source watermarks."""

from alembic import op

revision = "20261009_graph_sync"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS graph_sync")
    op.execute("""CREATE TABLE graph_sync.entities (
        entity_key text PRIMARY KEY, project_id text NOT NULL,
        source_instance text NOT NULL, entity_type text NOT NULL, source_id text NOT NULL,
        revision bigint NOT NULL CHECK (revision >= 0), owner_id text,
        tombstoned boolean NOT NULL DEFAULT false, payload jsonb NOT NULL,
        updated_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (source_instance, project_id, entity_type, source_id))""")
    op.execute("""CREATE TABLE graph_sync.projection_outbox (
        id text PRIMARY KEY, entity_key text NOT NULL REFERENCES graph_sync.entities,
        project_id text NOT NULL, revision bigint NOT NULL, payload jsonb NOT NULL,
        status text NOT NULL DEFAULT 'queued', attempt_count integer NOT NULL DEFAULT 0,
        max_attempts integer NOT NULL DEFAULT 5, claim_token text,
        lease_expires_at timestamptz, next_attempt_at timestamptz NOT NULL DEFAULT now(),
        error_code text, created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now())""")
    op.execute("""CREATE INDEX graph_sync_due_jobs ON graph_sync.projection_outbox
        (status, next_attempt_at)""")
    op.execute("CREATE INDEX graph_sync_leases ON graph_sync.projection_outbox (lease_expires_at)")
    op.execute("""CREATE TABLE graph_sync.event_receipts (
        id text PRIMARY KEY, source_instance text NOT NULL, event_id text NOT NULL,
        project_id text NOT NULL, fingerprint text NOT NULL, status text NOT NULL,
        job_id text REFERENCES graph_sync.projection_outbox,
        created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(source_instance,event_id))""")
    op.execute("""CREATE TABLE graph_sync.audit_events (
        id text PRIMARY KEY, actor_id text NOT NULL, project_id text NOT NULL,
        action text NOT NULL, resource_id text NOT NULL, details jsonb NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now())""")
    op.execute("""CREATE TABLE graph_sync.sync_watermarks (
        source_instance text NOT NULL, project_id text NOT NULL, resource_type text NOT NULL,
        cursor jsonb NOT NULL, updated_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(source_instance,project_id,resource_type))""")


def downgrade() -> None:
    for table in ("sync_watermarks", "audit_events", "event_receipts", "projection_outbox", "entities"):
        op.execute(f"DROP TABLE graph_sync.{table}")
