"""Versioned sources, case assets, review links and simulated external writes."""

from alembic import op

revision = "20261014_cases"
down_revision = "20261014_deadline"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE business.case_sources (
      id text PRIMARY KEY, project_id text NOT NULL, user_id text NOT NULL,
      kind text NOT NULL CHECK(kind IN ('requirement','openapi')), title text NOT NULL,
      content text NOT NULL, content_hash text NOT NULL, version integer NOT NULL DEFAULT 1,
      deleted boolean NOT NULL DEFAULT false, updated_at timestamptz NOT NULL DEFAULT now())""")
    op.execute("""CREATE TABLE business.case_assets (
      id text PRIMARY KEY, task_id text NOT NULL REFERENCES business.tasks(id),
      project_id text NOT NULL, user_id text NOT NULL, ordinal integer NOT NULL,
      body jsonb NOT NULL, sources jsonb NOT NULL, evidence jsonb NOT NULL DEFAULT '[]',
      version integer NOT NULL DEFAULT 1, content_hash text NOT NULL,
      decision text NOT NULL DEFAULT 'pending', reviewer_id text,
      created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(task_id,ordinal))""")
    op.execute("""CREATE TABLE business.case_asset_versions (
      asset_id text NOT NULL REFERENCES business.case_assets(id), version integer NOT NULL,
      body jsonb NOT NULL, content_hash text NOT NULL, actor_id text NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(asset_id,version))""")
    op.execute("ALTER TABLE business.approvals ADD COLUMN asset_id text REFERENCES business.case_assets(id)")
    op.execute("CREATE UNIQUE INDEX case_approval_version ON business.approvals(asset_id,asset_version) WHERE asset_id IS NOT NULL")
    op.execute("ALTER TABLE business.external_operations ADD COLUMN asset_id text REFERENCES business.case_assets(id), ADD COLUMN content_hash text, ADD COLUMN payload jsonb, ADD COLUMN mode text NOT NULL DEFAULT 'disabled', ADD COLUMN error_code text, ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now()")
    op.execute("""CREATE TABLE business.demo_zentao_cases (
      id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      operation_id text NOT NULL UNIQUE REFERENCES business.external_operations(id),
      asset_id text NOT NULL REFERENCES business.case_assets(id), project_id text NOT NULL,
      body jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now())""")
    op.execute("CREATE TABLE business.source_watermarks (source_id text PRIMARY KEY REFERENCES business.case_sources(id), version integer NOT NULL)")


def downgrade():
    op.execute("DROP TABLE business.source_watermarks, business.demo_zentao_cases")
    op.execute("ALTER TABLE business.external_operations DROP COLUMN asset_id, DROP COLUMN content_hash, DROP COLUMN payload, DROP COLUMN mode, DROP COLUMN error_code, DROP COLUMN updated_at")
    op.execute("ALTER TABLE business.approvals DROP COLUMN asset_id")
    op.execute("DROP TABLE business.case_asset_versions, business.case_assets, business.case_sources")
