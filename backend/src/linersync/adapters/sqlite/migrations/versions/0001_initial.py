"""initial schema

Revision ID: 0001
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "approvals",
        sa.Column("sync_id", sa.String(), primary_key=True),
        sa.Column("member_id", sa.String(), primary_key=True),
        sa.Column("fingerprint", sa.String(), primary_key=True),
    )
    op.create_table(
        "attention",
        sa.Column("item_id", sa.String(), primary_key=True),
        sa.Column("dedupe_key", sa.String(), nullable=False),
        sa.Column("resolved_at", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
    )
    op.create_index("ix_attention_dedupe_key", "attention", ["dedupe_key"])
    op.create_table(
        "breakers",
        sa.Column("service", sa.String(), primary_key=True),
        sa.Column("body", sa.Text(), nullable=False),
    )
    op.create_table(
        "connections",
        sa.Column("service", sa.String(), primary_key=True),
        sa.Column("body", sa.Text(), nullable=False),
    )
    op.create_table(
        "credentials",
        sa.Column("service", sa.String(), primary_key=True),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
    )
    op.create_table(
        "jobs",
        sa.Column("job_id", sa.String(), primary_key=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("sync_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("finished_at", sa.String(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
    )
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index("ix_jobs_sync_id", "jobs", ["sync_id"])
    op.create_table(
        "pre_write_snapshots",
        sa.Column("run_id", sa.String(), primary_key=True),
        sa.Column("member_id", sa.String(), primary_key=True),
        sa.Column("body", sa.Text(), nullable=True),
    )
    op.create_table(
        "quota_usage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("service", sa.String(), nullable=False),
        sa.Column("units", sa.Integer(), nullable=False),
        sa.Column("at", sa.String(), nullable=False),
    )
    op.create_index("ix_quota_usage_service_at", "quota_usage", ["service", "at"])
    op.create_table(
        "remembered_matches",
        sa.Column("source_service", sa.String(), primary_key=True),
        sa.Column("source_track_id", sa.String(), primary_key=True),
        sa.Column("target_service", sa.String(), primary_key=True),
        sa.Column("target_track_id", sa.String(), nullable=True),
    )
    op.create_table(
        "run_contexts",
        sa.Column("run_id", sa.String(), primary_key=True),
        sa.Column("body", sa.Text(), nullable=False),
    )
    op.create_table(
        "runs",
        sa.Column("run_id", sa.String(), primary_key=True),
        sa.Column("sync_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("started_at", sa.String(), nullable=False),
        sa.Column("saved_seq", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
    )
    op.create_index("ix_runs_sync_id", "runs", ["sync_id"])
    op.create_table(
        "sessions",
        sa.Column("token_hash", sa.String(), primary_key=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("expires_at", sa.String(), nullable=False),
    )
    op.create_table(
        "settings",
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("body", sa.Text(), nullable=False),
    )
    op.create_table(
        "syncs",
        sa.Column("sync_id", sa.String(), primary_key=True),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("syncs")
    op.drop_table("settings")
    op.drop_table("sessions")
    op.drop_table("runs")
    op.drop_table("run_contexts")
    op.drop_table("remembered_matches")
    op.drop_table("quota_usage")
    op.drop_table("pre_write_snapshots")
    op.drop_table("jobs")
    op.drop_table("credentials")
    op.drop_table("connections")
    op.drop_table("breakers")
    op.drop_table("attention")
    op.drop_table("approvals")
