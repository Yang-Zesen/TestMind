"""Explicit PostgreSQL project grants; absence means deny."""

from alembic import op

revision = "20261009_permissions"
down_revision = "20261009_maintenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE graph_sync.project_memberships (
        project_id text NOT NULL,user_id text NOT NULL,
        role text NOT NULL CHECK(role IN ('employee','manager','tse')),
        can_maintain boolean NOT NULL DEFAULT false,revoked boolean NOT NULL DEFAULT false,
        permission_version bigint NOT NULL DEFAULT 1 CHECK(permission_version>0),
        updated_at timestamptz NOT NULL DEFAULT now(),PRIMARY KEY(project_id,user_id))""")


def downgrade() -> None:
    op.execute("DROP TABLE graph_sync.project_memberships")
