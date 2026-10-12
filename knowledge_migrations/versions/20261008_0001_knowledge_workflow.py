"""Create the isolated knowledge ingestion schema."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261008_0001"
down_revision = None
branch_labels = ("knowledge",)
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS knowledge")
    op.create_table(
        "knowledge_bases",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="knowledge",
    )
    op.create_table(
        "documents",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("kb_id", sa.Text(), sa.ForeignKey("knowledge.knowledge_bases.id"), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("tombstoned_at", sa.DateTime(timezone=True)),
        schema="knowledge",
    )
    op.create_index("ix_knowledge_documents_scope", "documents", ["project_id", "kb_id", "created_at", "id"], schema="knowledge")
    op.create_table(
        "document_versions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("document_id", sa.Text(), sa.ForeignKey("knowledge.documents.id"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("document_version", sa.Text(), nullable=False),
        sa.Column("index_version", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("request_fingerprint", sa.Text(), nullable=False),
        sa.Column("quality_report", postgresql.JSONB()),
        sa.Column("index_report", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("document_id", "version_number", name="uq_knowledge_document_version_number"),
        schema="knowledge",
    )
    op.create_table(
        "jobs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("kb_id", sa.Text(), nullable=False),
        sa.Column("document_id", sa.Text(), sa.ForeignKey("knowledge.documents.id"), nullable=False),
        sa.Column("version_id", sa.Text(), sa.ForeignKey("knowledge.document_versions.id"), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text()),
        sa.Column("request_fingerprint", sa.Text(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("lease_owner", sa.Text()),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("correlation_id", sa.Text(), nullable=False),
        sa.Column("error_code", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("project_id", "idempotency_key", name="uq_knowledge_project_idempotency"),
        schema="knowledge",
    )
    op.create_index("ix_knowledge_jobs_status_next", "jobs", ["status", "next_attempt_at"], schema="knowledge")
    op.create_index("ix_knowledge_jobs_lease_expiry", "jobs", ["lease_expires_at"], schema="knowledge")
    op.create_table(
        "change_receipts",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_event_id", sa.Text(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("kb_id", sa.Text(), nullable=False),
        sa.Column("resource_type", sa.Text(), nullable=False),
        sa.Column("resource_id", sa.Text(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("source_version", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("job_id", sa.Text(), sa.ForeignKey("knowledge.jobs.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("source", "source_event_id", name="uq_knowledge_source_event"),
        schema="knowledge",
    )
    op.create_table(
        "project_active_versions",
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("kb_id", sa.Text(), nullable=False),
        sa.Column("document_id", sa.Text(), sa.ForeignKey("knowledge.documents.id"), nullable=False),
        sa.Column("document_version", sa.Text(), nullable=False),
        sa.Column("index_version", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("project_id", "kb_id", "document_id", name="pk_knowledge_active_version"),
        schema="knowledge",
    )
    op.create_table(
        "activation_operations",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("job_id", sa.Text(), sa.ForeignKey("knowledge.jobs.id"), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("kb_id", sa.Text(), nullable=False),
        sa.Column("document_id", sa.Text(), nullable=False),
        sa.Column("from_document_version", sa.Text()),
        sa.Column("from_index_version", sa.Text()),
        sa.Column("to_document_version", sa.Text(), nullable=False),
        sa.Column("to_index_version", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="knowledge",
    )
    op.create_table(
        "tombstones",
        sa.Column("document_id", sa.Text(), primary_key=True),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("kb_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("cleanup_completed_at", sa.DateTime(timezone=True)),
        schema="knowledge",
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("actor_id", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("kb_id", sa.Text(), nullable=False),
        sa.Column("resource_id", sa.Text(), nullable=False),
        sa.Column("correlation_id", sa.Text(), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="knowledge",
    )
    op.create_table(
        "invalidation_outbox",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("dedupe_key", sa.Text(), nullable=False, unique=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="knowledge",
    )


def downgrade() -> None:
    op.drop_table("invalidation_outbox", schema="knowledge")
    op.drop_table("audit_events", schema="knowledge")
    op.drop_table("tombstones", schema="knowledge")
    op.drop_table("activation_operations", schema="knowledge")
    op.drop_table("project_active_versions", schema="knowledge")
    op.drop_table("change_receipts", schema="knowledge")
    op.drop_table("jobs", schema="knowledge")
    op.drop_table("document_versions", schema="knowledge")
    op.drop_index("ix_knowledge_documents_scope", table_name="documents", schema="knowledge")
    op.drop_table("documents", schema="knowledge")
    op.drop_table("knowledge_bases", schema="knowledge")
