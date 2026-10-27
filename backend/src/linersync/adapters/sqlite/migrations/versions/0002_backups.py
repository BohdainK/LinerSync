"""backup store

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "backup_playlists",
        sa.Column("entry_id", sa.String(), primary_key=True),
        sa.Column("service", sa.String(), nullable=False),
        sa.Column("playlist_id", sa.String(), nullable=False),
        sa.Column("archived_at", sa.String(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "service", "playlist_id", name="uq_backup_playlists_service_playlist"
        ),
    )
    op.create_index("ix_backup_playlists_service", "backup_playlists", ["service"])
    op.create_table(
        "backup_versions",
        sa.Column("version_id", sa.String(), primary_key=True),
        sa.Column("entry_id", sa.String(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("cover_sha256", sa.String(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
    )
    op.create_index("ix_backup_versions_entry_id", "backup_versions", ["entry_id"])
    op.create_table(
        "backup_runs",
        sa.Column("backup_id", sa.String(), primary_key=True),
        sa.Column("service", sa.String(), nullable=False),
        sa.Column("started_at", sa.String(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
    )
    op.create_index("ix_backup_runs_service", "backup_runs", ["service"])
    op.create_table(
        "covers",
        sa.Column("sha256", sa.String(), primary_key=True),
        sa.Column("media_type", sa.String(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("covers")
    op.drop_index("ix_backup_runs_service", table_name="backup_runs")
    op.drop_table("backup_runs")
    op.drop_index("ix_backup_versions_entry_id", table_name="backup_versions")
    op.drop_table("backup_versions")
    op.drop_index("ix_backup_playlists_service", table_name="backup_playlists")
    op.drop_table("backup_playlists")
