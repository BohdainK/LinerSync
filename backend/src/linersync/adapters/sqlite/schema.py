"""SQLAlchemy Core tables. `body` columns hold codec JSON text (plan 3 D5)."""

import sqlalchemy as sa

metadata = sa.MetaData()

syncs = sa.Table(
    "syncs",
    metadata,
    sa.Column("sync_id", sa.String, primary_key=True),
    sa.Column("seq", sa.Integer, nullable=False),
    sa.Column("body", sa.Text, nullable=False),
)

runs = sa.Table(
    "runs",
    metadata,
    sa.Column("run_id", sa.String, primary_key=True),
    sa.Column("sync_id", sa.String, nullable=False, index=True),
    sa.Column("status", sa.String, nullable=False),
    sa.Column("started_at", sa.String, nullable=False),
    sa.Column("saved_seq", sa.Integer, nullable=False),
    sa.Column("body", sa.Text, nullable=False),
)

pre_write_snapshots = sa.Table(
    "pre_write_snapshots",
    metadata,
    sa.Column("run_id", sa.String, primary_key=True),
    sa.Column("member_id", sa.String, primary_key=True),
    sa.Column("body", sa.Text, nullable=True),
    # PriorState codec JSON; NULL = none kept (undo writes, older snapshots).
    sa.Column("prior", sa.Text, nullable=True),
)

remembered_matches = sa.Table(
    "remembered_matches",
    metadata,
    sa.Column("source_service", sa.String, primary_key=True),
    sa.Column("source_track_id", sa.String, primary_key=True),
    sa.Column("target_service", sa.String, primary_key=True),
    sa.Column("target_track_id", sa.String, nullable=True),
)

attention = sa.Table(
    "attention",
    metadata,
    sa.Column("item_id", sa.String, primary_key=True),
    sa.Column("dedupe_key", sa.String, nullable=False, index=True),
    sa.Column("resolved_at", sa.String, nullable=True),
    sa.Column("created_at", sa.String, nullable=False),
    sa.Column("body", sa.Text, nullable=False),
)

breakers = sa.Table(
    "breakers",
    metadata,
    sa.Column("service", sa.String, primary_key=True),
    sa.Column("body", sa.Text, nullable=False),
)

approvals = sa.Table(
    "approvals",
    metadata,
    sa.Column("sync_id", sa.String, primary_key=True),
    sa.Column("member_id", sa.String, primary_key=True),
    sa.Column("fingerprint", sa.String, primary_key=True),
)

credentials = sa.Table(
    "credentials",
    metadata,
    sa.Column("service", sa.String, primary_key=True),
    sa.Column("ciphertext", sa.LargeBinary, nullable=False),
)

quota_usage = sa.Table(
    "quota_usage",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("service", sa.String, nullable=False),
    sa.Column("units", sa.Integer, nullable=False),
    sa.Column("at", sa.String, nullable=False),
    sa.Index("ix_quota_usage_service_at", "service", "at"),
)

jobs = sa.Table(
    "jobs",
    metadata,
    sa.Column("job_id", sa.String, primary_key=True),
    sa.Column("status", sa.String, nullable=False, index=True),
    sa.Column("sync_id", sa.String, nullable=True, index=True),
    sa.Column("created_at", sa.String, nullable=False),
    sa.Column("finished_at", sa.String, nullable=True),
    sa.Column("body", sa.Text, nullable=False),
)

run_contexts = sa.Table(
    "run_contexts",
    metadata,
    sa.Column("run_id", sa.String, primary_key=True),
    sa.Column("body", sa.Text, nullable=False),
)

settings = sa.Table(
    "settings",
    metadata,
    sa.Column("key", sa.String, primary_key=True),
    sa.Column("body", sa.Text, nullable=False),
)

connections = sa.Table(
    "connections",
    metadata,
    sa.Column("service", sa.String, primary_key=True),
    sa.Column("body", sa.Text, nullable=False),
)

sessions = sa.Table(
    "sessions",
    metadata,
    sa.Column("token_hash", sa.String, primary_key=True),
    sa.Column("created_at", sa.String, nullable=False),
    sa.Column("expires_at", sa.String, nullable=False),
)

backup_playlists = sa.Table(
    "backup_playlists",
    metadata,
    sa.Column("entry_id", sa.String, primary_key=True),
    sa.Column("service", sa.String, nullable=False, index=True),
    sa.Column("playlist_id", sa.String, nullable=False),
    sa.Column("archived_at", sa.String, nullable=True),
    sa.Column("body", sa.Text, nullable=False),
    sa.UniqueConstraint(
        "service", "playlist_id", name="uq_backup_playlists_service_playlist"
    ),
)

backup_versions = sa.Table(
    "backup_versions",
    metadata,
    sa.Column("version_id", sa.String, primary_key=True),
    sa.Column("entry_id", sa.String, nullable=False, index=True),
    sa.Column("seq", sa.Integer, nullable=False),
    sa.Column("cover_sha256", sa.String, nullable=True),
    sa.Column("body", sa.Text, nullable=False),
)

backup_runs = sa.Table(
    "backup_runs",
    metadata,
    sa.Column("backup_id", sa.String, primary_key=True),
    sa.Column("service", sa.String, nullable=False, index=True),
    sa.Column("started_at", sa.String, nullable=False),
    sa.Column("body", sa.Text, nullable=False),
)

covers = sa.Table(
    "covers",
    metadata,
    sa.Column("sha256", sa.String, primary_key=True),
    sa.Column("media_type", sa.String, nullable=False),
    sa.Column("data", sa.LargeBinary, nullable=False),
)
