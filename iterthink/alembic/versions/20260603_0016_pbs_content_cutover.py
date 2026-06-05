"""PBS v0.0.7 content cutover: drop legacy document tables, create content projection.

Revision ID: 20260603_0016
Revises: 20260517_0015
Create Date: 2026-06-03

No backwards compatibility — existing ORM rows are discarded.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260603_0016"
down_revision: Union[str, None] = "20260517_0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _drop_if_exists(inspector: sa.Inspector, name: str) -> None:
    if inspector.has_table(name):
        op.drop_table(name)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    for name in (
        "impact_annotations",
        "paragraph_user_comments",
        "document_versions",
        "documents",
        "sync_outbox",
        "rag_sync_outbox",
        "content_changes",
        "content_file_links",
        "content_geometries",
        "content_relations",
        "content",
        "files",
        "projects",
        "workspaces",
    ):
        _drop_if_exists(inspector, name)
        inspector = sa.inspect(bind)

    op.create_table(
        "workspaces",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("root_path", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_projects_workspace_id", "projects", ["workspace_id"], unique=False)

    op.create_table(
        "files",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("storage_relpath", sa.String(length=512), nullable=False),
        sa.Column("media_format", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "project_id", "storage_relpath", name="uq_files_storage"),
    )

    op.create_table(
        "content",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("lineage_id", sa.String(length=36), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("is_latest", sa.Boolean(), nullable=False),
        sa.Column("supersedes_content_id", sa.Integer(), nullable=True),
        sa.Column("contract_id", sa.String(length=36), nullable=False),
        sa.Column("ingestion_job_id", sa.Integer(), nullable=True),
        sa.Column("content_kind", sa.String(length=100), nullable=False),
        sa.Column("canonical_type", sa.String(length=100), nullable=True),
        sa.Column("canonical_type_version", sa.String(length=16), nullable=False),
        sa.Column("code_or_number", sa.String(length=255), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        sa.Column("storey", sa.String(length=100), nullable=True),
        sa.Column("source_system", sa.String(length=100), nullable=True),
        sa.Column("source_id", sa.String(length=255), nullable=True),
        sa.Column("external_ref", sa.String(length=255), nullable=True),
        sa.Column("attributes", sa.Text(), nullable=True),
        sa.Column("provenance", sa.Text(), nullable=True),
        sa.Column("last_disk_mtime_ns", sa.BigInteger(), nullable=True),
        sa.Column("last_disk_size", sa.Integer(), nullable=True),
        sa.Column("last_disk_sha256", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["supersedes_content_id"], ["content.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("contract_id"),
    )
    op.create_index("ix_content_workspace_id", "content", ["workspace_id"], unique=False)
    op.create_index("ix_content_lineage_id", "content", ["lineage_id"], unique=False)
    op.create_index(
        "ix_content_workspace_project_lineage_version",
        "content",
        ["workspace_id", "project_id", "lineage_id", "version_no"],
        unique=True,
    )

    op.create_table(
        "content_file_links",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("content_id", sa.Integer(), nullable=False),
        sa.Column("file_id", sa.Integer(), nullable=False),
        sa.Column("relation_type", sa.String(length=100), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["content_id"], ["content.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["file_id"], ["files.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("content_id", "file_id", "relation_type", name="uq_content_file_relation"),
    )

    op.create_table(
        "content_geometries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("content_id", sa.Integer(), nullable=False),
        sa.Column("geometry_role", sa.String(length=100), nullable=False),
        sa.Column("geometry_source", sa.String(length=100), nullable=True),
        sa.Column("geometry_space", sa.String(length=100), nullable=False),
        sa.Column("geom", sa.Text(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["content_id"], ["content.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "content_relations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("lineage_id", sa.String(length=36), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("is_latest", sa.Boolean(), nullable=False),
        sa.Column("supersedes_content_id", sa.Integer(), nullable=True),
        sa.Column("source_content_id", sa.Integer(), nullable=False),
        sa.Column("target_content_id", sa.Integer(), nullable=False),
        sa.Column("relation_type", sa.String(length=100), nullable=False),
        sa.Column("attributes", sa.Text(), nullable=True),
        sa.Column("provenance", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["source_content_id"], ["content.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_content_id"], ["content.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "content_changes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("content_version_id", sa.Integer(), nullable=False),
        sa.Column("lineage_id", sa.String(length=36), nullable=False),
        sa.Column("change_class", sa.String(length=64), nullable=False),
        sa.Column("change_type", sa.String(length=64), nullable=False),
        sa.Column("from_revision", sa.Integer(), nullable=False),
        sa.Column("to_revision", sa.Integer(), nullable=False),
        sa.Column("affected_subject_id", sa.String(length=255), nullable=False),
        sa.Column("affected_subject_type", sa.String(length=100), nullable=False),
        sa.Column("property_path", sa.String(length=512), nullable=True),
        sa.Column("property_path_kind", sa.String(length=64), nullable=True),
        sa.Column("from_value", sa.Text(), nullable=True),
        sa.Column("to_value", sa.Text(), nullable=True),
        sa.Column("intent_verdict", sa.String(length=16), nullable=True),
        sa.Column("artifact_storage_link", sa.String(length=512), nullable=True),
        sa.Column("detected_at", sa.Float(), nullable=False),
        sa.Column("change_source", sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "sync_outbox",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("entity_table", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("op", sa.String(length=16), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("pushed_at", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "rag_sync_outbox",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("content_version_id", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=True),
        sa.Column("op", sa.String(length=16), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("pushed_at", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )

    op.create_table(
        "paragraph_user_comments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("content_version_id", sa.Integer(), nullable=False),
        sa.Column("paragraph_index", sa.Integer(), nullable=False),
        sa.Column("annotation_kind", sa.String(length=24), nullable=False),
        sa.Column("plan_page_index", sa.Integer(), nullable=True),
        sa.Column("plan_norm_x", sa.Float(), nullable=True),
        sa.Column("plan_norm_y", sa.Float(), nullable=True),
        sa.Column("geometry_json", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_paragraph_user_comment_paragraph",
        "paragraph_user_comments",
        ["content_version_id", "paragraph_index"],
        unique=True,
        sqlite_where=sa.text("annotation_kind = 'paragraph'"),
    )

    op.create_table(
        "impact_annotations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("content_version_id", sa.Integer(), nullable=False),
        sa.Column("paragraph_index", sa.Integer(), nullable=False),
        sa.Column("prompt_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("details_json", sa.Text(), nullable=True),
        sa.Column("overridden", sa.Boolean(), nullable=False),
        sa.Column("override_comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "content_version_id",
            "paragraph_index",
            "prompt_id",
            name="uq_impact_annotation_key",
        ),
    )

    # paragraph_analysis + credential_vault unchanged if present — recreate if dropped
    if not inspector.has_table("paragraph_analysis"):
        op.create_table(
            "paragraph_analysis",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("check_id", sa.String(length=64), nullable=False),
            sa.Column("old_sha256", sa.String(length=64), nullable=False),
            sa.Column("new_sha256", sa.String(length=64), nullable=False),
            sa.Column("model", sa.String(length=128), nullable=False),
            sa.Column("document_path_key", sa.String(length=64), nullable=False),
            sa.Column("result_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "check_id",
                "old_sha256",
                "new_sha256",
                "model",
                "document_path_key",
                name="uq_paragraph_analysis_path_key",
            ),
        )

    if not inspector.has_table("credential_vault"):
        op.create_table(
            "credential_vault",
            sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
            sa.Column("kdf_salt", sa.LargeBinary(), nullable=False),
            sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
            sa.Column("verifier", sa.LargeBinary(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    op.execute("INSERT INTO workspaces (id, name, created_at) VALUES (1, 'default', 0)")
    op.execute("INSERT INTO projects (id, workspace_id, name, created_at) VALUES (1, 1, 'default', 0)")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for name in (
        "impact_annotations",
        "paragraph_user_comments",
        "app_settings",
        "rag_sync_outbox",
        "sync_outbox",
        "content_changes",
        "content_file_links",
        "content_geometries",
        "content_relations",
        "content",
        "files",
        "projects",
        "workspaces",
    ):
        _drop_if_exists(inspector, name)
        inspector = sa.inspect(bind)
