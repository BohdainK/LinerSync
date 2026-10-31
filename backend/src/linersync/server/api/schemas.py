"""Response and request models for the JSON API."""

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from linersync.application.settings import (
    DEFAULT_BACKUP_SCHEDULE,
    DEFAULT_VERSIONS_KEPT,
)
from linersync.domain.attention import AttentionKind
from linersync.domain.backups import BackedUpPlaylist, BackupRun, PlaylistVersion
from linersync.domain.jobs import Job
from linersync.domain.operations import AddTracks, RemoveTracks
from linersync.domain.playlists import PlaylistMeta
from linersync.domain.runs import MemberStatus, SyncRun
from linersync.domain.sync import SyncMode
from linersync.server.connections import ConnectionInfo


class ConnectionOut(BaseModel):
    service: str
    display_name: str
    category: str
    capabilities: list[str]
    experimental: bool
    status: str
    message: str
    account_name: str | None
    checked_at: datetime | None
    login_expires_at: datetime | None
    login_expiry_estimated: bool  # a reminder date rather than a deadline
    values: dict[str, str]
    secrets_set: list[str]

    @classmethod
    def of(cls, info: ConnectionInfo) -> "ConnectionOut":
        return cls(
            service=info.service,
            display_name=info.display_name,
            category=info.category,
            capabilities=[c.value for c in info.capabilities],
            experimental=info.experimental,
            status=info.status.value,
            message=info.message,
            account_name=info.account_name,
            checked_at=info.checked_at,
            login_expires_at=info.login_expires_at,
            login_expiry_estimated=info.login_expiry_estimated,
            values=dict(info.values),
            secrets_set=list(info.secrets_set),
        )


class LoadErrorOut(BaseModel):
    name: str
    message: str


class ConnectionListOut(BaseModel):
    services: list[ConnectionOut]
    load_errors: list[LoadErrorOut]


class CredentialsIn(BaseModel):
    values: dict[str, str]


class ConnectionTestOut(BaseModel):
    ok: bool
    message: str
    account_name: str | None


class OAuthStartOut(BaseModel):
    url: str


class DeviceCodeOut(BaseModel):
    user_code: str
    verification_url: str
    interval_s: int
    expires_in_s: int


class DevicePollOut(BaseModel):
    status: Literal["pending", "done", "denied", "expired"]
    message: str
    interval_s: int | None = None  # poll this often from now on


class SignInConfigOut(BaseModel):
    config: dict[str, str]


class SignInResultIn(BaseModel):
    result: dict[str, str]


class PlaylistOut(BaseModel):
    service: str
    playlist_id: str
    name: str
    description: str
    cover_url: str | None
    owned: bool
    readable: bool
    unreadable_reason: str | None
    track_count: int | None

    @classmethod
    def of(cls, meta: PlaylistMeta) -> "PlaylistOut":
        return cls(
            service=meta.service,
            playlist_id=meta.playlist_id,
            name=meta.name,
            description=meta.description,
            cover_url=meta.cover_url,
            owned=meta.owned,
            readable=meta.readable,
            unreadable_reason=meta.unreadable_reason,
            track_count=meta.track_count,
        )


# -- syncs ------------------------------------------------------------------
SyncName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]


class DestinationIn(BaseModel):
    service: str
    playlist_id: str | None = None
    mode: SyncMode = SyncMode.FULL


class MemberIn(BaseModel):
    service: str
    playlist_id: str


class SyncCreateIn(BaseModel):
    name: SyncName
    source: MemberIn
    destinations: list[DestinationIn] = Field(min_length=1, max_length=10)
    schedule: str | None = None


class SyncBulkIn(BaseModel):
    syncs: list[SyncCreateIn]


class SyncPatchIn(BaseModel):
    """`"schedule": null` resets to the default; an absent key keeps it."""

    name: SyncName | None = None
    schedule: str | None = None
    paused: bool | None = None
    modes: dict[str, SyncMode] | None = None
    add_destinations: list[DestinationIn] | None = None
    remove_member_ids: list[str] | None = None


class RunIn(BaseModel):
    dry_run: bool = False


class JobProgressOut(BaseModel):
    phase: str
    done: int
    total: int
    message: str


class JobOut(BaseModel):
    job_id: str
    kind: str
    status: str
    label: str
    sync_id: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    run_ids: list[str]
    backup_ids: list[str]
    error: str | None
    progress: JobProgressOut | None
    cancel_requested: bool

    @classmethod
    def of(cls, job: Job) -> "JobOut":
        p = job.progress
        return cls(
            job_id=job.job_id,
            kind=job.kind,
            status=job.status.value,
            label=job.label,
            sync_id=job.sync_id,
            created_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
            run_ids=list(job.run_ids),
            backup_ids=list(job.backup_ids),
            error=job.error,
            progress=None
            if p is None
            else JobProgressOut(
                phase=p.phase, done=p.done, total=p.total, message=p.message
            ),
            cancel_requested=job.cancel_requested,
        )


class RunSummaryOut(BaseModel):
    run_id: str
    sync_id: str
    kind: str
    trigger: str
    dry_run: bool
    status: str
    started_at: datetime
    finished_at: datetime | None
    error: str | None
    added: int
    removed: int
    not_made: int
    unmatched: int


def run_summary(run: SyncRun) -> RunSummaryOut:
    added = removed = not_made = unmatched = 0
    if run.plan is not None:
        for plan in run.plan.member_plans:
            for op in plan.operations:
                if isinstance(op, AddTracks):
                    added += len(op.track_ids)
                elif isinstance(op, RemoveTracks):
                    removed += len(op.removals)
        not_made = sum(len(m.not_made) for m in run.members)
        unmatched = len(run.plan.unresolved)
    return RunSummaryOut(
        run_id=run.run_id,
        sync_id=run.sync_id,
        kind=run.kind.value,
        trigger=run.trigger.value,
        dry_run=run.dry_run,
        status=run.status.value,
        started_at=run.started_at,
        finished_at=run.finished_at,
        error=run.error,
        added=added,
        removed=removed,
        not_made=not_made,
        unmatched=unmatched,
    )


class SourceOut(BaseModel):
    member_id: str
    service: str
    playlist_id: str | None


class DestinationOut(BaseModel):
    member_id: str
    service: str
    playlist_id: str | None
    mode: SyncMode
    effective_mode: SyncMode
    limitations: list[str]
    last_synced_at: datetime | None
    missing: bool


class SyncOut(BaseModel):
    sync_id: str
    name: str
    status: str
    pause_reason: str | None
    schedule: str | None
    effective_schedule: str
    next_run_at: datetime | None
    source: SourceOut
    destinations: list[DestinationOut]
    active_job: JobOut | None
    last_run: RunSummaryOut | None


# -- runs -------------------------------------------------------------------
class TrackOut(BaseModel):
    service: str
    track_id: str
    title: str
    artists: list[str]
    album: str | None
    duration_ms: int | None
    isrc: str | None
    url: str | None
    known: bool


OperationType = Literal[
    "create_playlist", "update_metadata", "add_tracks", "reorder", "remove_tracks"
]


class OperationOut(BaseModel):
    type: OperationType
    op_id: str
    name: str | None = None
    description: str | None = None
    cover_url: str | None = None
    tracks: list[TrackOut] = []
    positions: list[int] = []
    order_length: int | None = None


class ChangeOut(BaseModel):
    kind: str
    detail: str
    track: TrackOut | None


class CandidateOut(BaseModel):
    track: TrackOut
    confidence: float
    reasons: list[str]


class UnresolvedOut(BaseModel):
    member_id: str
    position: int
    source: TrackOut
    reasons: list[str]
    candidates: list[CandidateOut]


class MismatchOut(BaseModel):
    missing: list[TrackOut]
    unexpected: list[TrackOut]


class MemberOut(BaseModel):
    member_id: str
    service: str | None
    playlist_id: str | None
    status: MemberStatus | None
    error: str | None
    rebuilt: bool
    created_playlist_id: str | None
    operations: list[OperationOut]
    not_made: list[ChangeOut]
    mismatch: MismatchOut | None


class RunOut(RunSummaryOut):
    members: list[MemberOut]
    unresolved: list[UnresolvedOut]
    undoes_run_id: str | None
    undoable: bool


# -- transfers --------------------------------------------------------------
class TransferIn(BaseModel):
    source_service: str
    playlist_id: str
    destination: str
    dry_run: bool = False


class BulkItemIn(BaseModel):
    source_service: str
    playlist_id: str
    destination_services: list[str] = Field(min_length=1)


class BulkTransferIn(BaseModel):
    items: list[BulkItemIn] = Field(min_length=1)
    dry_run: bool = False


class EstimateIn(BaseModel):
    items: list[BulkItemIn]


class EstimateOut(BaseModel):
    playlists: int
    songs: int
    api_calls: dict[str, int]
    add_only_destinations: list[str]
    unreadable: list[PlaylistOut]


# -- attention, home and settings -------------------------------------------
class AttentionOut(BaseModel):
    item_id: str
    kind: AttentionKind
    created_at: datetime
    detail: str
    sync_id: str | None
    sync_name: str | None
    member_id: str | None
    service: str | None
    source: TrackOut | None
    target_service: str | None
    candidates: list[CandidateOut]
    not_made: list[ChangeOut]


class PickIn(BaseModel):
    target_track_id: str


class RunNowIn(BaseModel):
    run_now: bool = True


class ActionOut(BaseModel):
    job: JobOut | None


class WarningOut(BaseModel):
    service: str
    kind: Literal["needs_sign_in", "failing", "login_expiring", "service_paused"]
    message: str


class LimitOut(BaseModel):
    service: str
    display_name: str
    used: int
    limit: int | None
    resets_at: datetime


class NextUpOut(BaseModel):
    kind: Literal["sync", "backup"]
    sync_id: str | None
    name: str
    at: datetime


class HomeOut(BaseModel):
    running: list[JobOut]
    queued: list[JobOut]
    next_up: list[NextUpOut]
    recent: list[JobOut]
    attention_count: int
    warnings: list[WarningOut]
    api_limits: list[LimitOut]


class LibraryServiceOut(BaseModel):
    service: str
    display_name: str
    playlists: int
    songs: int
    archived: int
    versions: int


class ActivityOut(BaseModel):
    sync_runs: int
    transfer_runs: int
    undo_runs: int
    succeeded: int
    partial: int
    failed: int
    waiting: int
    skipped: int
    songs_added: int
    songs_removed: int
    backups: int


class DayOut(BaseModel):
    day: date
    succeeded: int
    partial: int
    failed: int


class MatchingOut(BaseModel):
    remembered: int
    not_on_service: int
    open_attention: int


class StatsOut(BaseModel):
    days: int
    since: datetime
    library: list[LibraryServiceOut]
    syncs: int
    paused_syncs: int
    activity: ActivityOut
    daily: list[DayOut]
    last_backup_at: datetime | None
    matching: MatchingOut


class SettingsIn(BaseModel):
    default_schedule: str
    brake_max_fraction: float = Field(gt=0, le=1)
    brake_max_count: int = Field(ge=0)
    match_accept_threshold: float = Field(ge=0, le=1)
    match_min_margin: float = Field(ge=0, le=1)
    breaker_failure_threshold: int = Field(ge=1)
    breaker_cooldown_minutes: int = Field(ge=1)
    backup_schedule: str | None = DEFAULT_BACKUP_SCHEDULE
    backup_versions_kept: int = Field(default=DEFAULT_VERSIONS_KEPT, ge=1, le=100)


class SettingsOut(BaseModel):
    default_schedule: str
    timezone: str
    brake_max_fraction: float
    brake_max_count: int
    match_accept_threshold: float
    match_min_margin: float
    breaker_failure_threshold: int
    breaker_cooldown_minutes: int
    backup_schedule: str | None
    backup_versions_kept: int


class BackupRunOut(BaseModel):
    backup_id: str
    service: str
    started_at: datetime
    finished_at: datetime
    status: str
    playlists: int
    saved: int
    unchanged: int
    archived: int
    unreadable: int
    errors: list[str]
    error: str | None

    @classmethod
    def of(cls, run: BackupRun) -> "BackupRunOut":
        return cls(
            backup_id=run.backup_id,
            service=run.service,
            started_at=run.started_at,
            finished_at=run.finished_at,
            status=run.status.value,
            playlists=run.playlists,
            saved=run.saved,
            unchanged=run.unchanged,
            archived=run.archived,
            unreadable=run.unreadable,
            errors=list(run.errors),
            error=run.error,
        )


class BackupServiceOut(BaseModel):
    service: str
    display_name: str
    connected: bool
    playlists: int
    archived: int
    missing: int
    last_run: BackupRunOut | None


class BackupSummaryOut(BaseModel):
    services: list[BackupServiceOut]
    schedule: str | None
    next_backup_at: datetime | None
    active_job: JobOut | None


class BackupRunIn(BaseModel):
    services: list[str] = []


# -- the backup archive -----------------------------------------------------
class BackupPlaylistOut(BaseModel):
    entry_id: str
    service: str
    playlist_id: str
    name: str
    track_count: int
    versions: int
    first_backed_up_at: datetime
    last_seen_at: datetime
    missing_since: datetime | None
    archived_at: datetime | None
    cover_url: str | None

    @classmethod
    def of(cls, entry: BackedUpPlaylist) -> "BackupPlaylistOut":
        return cls(
            entry_id=entry.entry_id,
            service=entry.service,
            playlist_id=entry.playlist_id,
            name=entry.name,
            track_count=entry.track_count,
            versions=entry.versions,
            first_backed_up_at=entry.first_backed_up_at,
            last_seen_at=entry.last_seen_at,
            missing_since=entry.missing_since,
            archived_at=entry.archived_at,
            cover_url=cover_path(entry.cover_sha256),
        )


class BackupVersionOut(BaseModel):
    version_id: str
    entry_id: str
    taken_at: datetime
    name: str
    description: str
    track_count: int
    cover_url: str | None

    @classmethod
    def of(cls, version: PlaylistVersion) -> "BackupVersionOut":
        meta = version.snapshot.meta
        return cls(
            version_id=version.version_id,
            entry_id=version.entry_id,
            taken_at=version.snapshot.taken_at,
            name=meta.name,
            description=meta.description,
            track_count=len(version.snapshot.entries),
            cover_url=cover_path(version.cover_sha256),
        )


class BackupPlaylistDetailOut(BackupPlaylistOut):
    history: list[BackupVersionOut]


class BackupTrackOut(BaseModel):
    position: int
    added_at: datetime | None
    track: TrackOut


class BackupVersionDetailOut(BackupVersionOut):
    tracks: list[BackupTrackOut]


class RestoreIn(BaseModel):
    destination: str | None = None
    dry_run: bool = False


# -- JSON export and import ---------------------------------------------------
class ExportOut(BaseModel):
    """Documents the export download's header; the body is the full
    ExportDocument (settings, syncs, runs, backups, ...)."""

    model_config = ConfigDict(extra="allow")

    format: str
    version: int
    exported_at: datetime
    app_version: str


class RestoreReportOut(BaseModel):
    added: dict[str, int]
    skipped: dict[str, int]
    settings_applied: bool


def cover_path(sha256: str | None) -> str | None:
    return None if sha256 is None else f"/api/backups/covers/{sha256}"
