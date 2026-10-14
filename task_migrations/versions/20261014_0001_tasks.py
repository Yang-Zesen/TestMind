"""Durable tasks, ordered events, approvals and local demo grants."""

from alembic import op

revision = "20261014_tasks"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE SCHEMA business")
    op.execute("""CREATE TABLE business.tasks (
        id text PRIMARY KEY, user_id text NOT NULL, project_id text NOT NULL,
        session_id text NOT NULL, query text NOT NULL, intent text,
        status text NOT NULL CHECK (status IN ('queued','running','waiting_input','waiting_review','completed','denied','failed','needs_reconciliation','cancelled','rejected')),
        trace_id text NOT NULL, execution_thread_id text NOT NULL UNIQUE,
        idempotency_key text NOT NULL, fingerprint text NOT NULL,
        version integer NOT NULL DEFAULT 1, input_version integer NOT NULL DEFAULT 1,
        event_seq bigint NOT NULL DEFAULT 0, attempt_count integer NOT NULL DEFAULT 0,
        max_attempts integer NOT NULL DEFAULT 3, claim_token text,
        lease_expires_at timestamptz, next_attempt_at timestamptz NOT NULL DEFAULT now(),
        cancel_requested boolean NOT NULL DEFAULT false,
        result jsonb NOT NULL DEFAULT '{}', error_code text,
        created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (user_id, project_id, idempotency_key))""")
    op.execute("CREATE INDEX business_tasks_due ON business.tasks(status,next_attempt_at,lease_expires_at)")
    op.execute("""CREATE TABLE business.task_events (
        task_id text NOT NULL REFERENCES business.tasks(id) ON DELETE CASCADE,
        seq bigint NOT NULL, status text NOT NULL, data jsonb NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(task_id,seq))""")
    op.execute("""CREATE TABLE business.approvals (
        id text PRIMARY KEY, task_id text NOT NULL REFERENCES business.tasks(id) ON DELETE CASCADE,
        project_id text NOT NULL, asset_version integer NOT NULL, content_hash text NOT NULL,
        decision text NOT NULL DEFAULT 'pending', reviewer_id text, valid boolean NOT NULL DEFAULT true,
        decided_at timestamptz, created_at timestamptz NOT NULL DEFAULT now())""")
    op.execute("""CREATE TABLE business.external_operations (
        id text PRIMARY KEY, task_id text NOT NULL REFERENCES business.tasks(id),
        project_id text NOT NULL, idempotency_key text NOT NULL, asset_version integer NOT NULL,
        status text NOT NULL CHECK(status IN ('prepared','in_flight','completed','needs_reconciliation','failed')),
        external_id text, UNIQUE(project_id,idempotency_key))""")
    op.execute("""CREATE TABLE business.conversation_turns (
        task_id text PRIMARY KEY REFERENCES business.tasks(id) ON DELETE CASCADE,
        user_id text NOT NULL, project_id text NOT NULL, session_id text NOT NULL,
        query text NOT NULL, answer text NOT NULL, created_at timestamptz NOT NULL DEFAULT now())""")
    op.execute("""CREATE TABLE business.demo_users (
        user_id text PRIMARY KEY, enabled boolean NOT NULL DEFAULT true)""")
    op.execute("""CREATE TABLE business.demo_grants (
        user_id text NOT NULL REFERENCES business.demo_users(user_id),
        project_id text NOT NULL, kb_id text NOT NULL,
        revoked boolean NOT NULL DEFAULT false, permission_version integer NOT NULL DEFAULT 1,
        PRIMARY KEY(user_id,project_id,kb_id))""")


def downgrade():
    op.execute("DROP SCHEMA business CASCADE")
