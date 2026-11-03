"""Jobs: queued units of work the server runs one at a time."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from linersync.domain.runs import Trigger
from linersync.domain.sync import MemberRole, SyncGroup
from linersync.domain.tracks import ServiceId, Track


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


ACTIVE_STATUSES = frozenset({JobStatus.QUEUED, JobStatus.RUNNING})


@dataclass(frozen=True, slots=True)
class SyncJob:
    sync_id: str
    trigger: Trigger
    dry_run: bool = False
    preview_run_id: str | None = None


@dataclass(frozen=True, slots=True)
class PreviewJob:
    group: SyncGroup


@dataclass(frozen=True, slots=True)
class BulkPreviewJob:
    groups: tuple[SyncGroup, ...]


@dataclass(frozen=True, slots=True)
class TransferJob:
    source_service: ServiceId
    playlist_id: str
    destination: ServiceId
    dry_run: bool = False
    preview_run_id: str | None = None


@dataclass(frozen=True, slots=True)
class BulkTransferJob:
    transfers: tuple[TransferJob, ...]


@dataclass(frozen=True, slots=True)
class UndoJob:
    run_id: str


@dataclass(frozen=True, slots=True)
class CopyToSourceJob:
    """Copy songs the user added on a destination into the source playlist.
    An empty `track_ids` means every song they added there."""

    sync_id: str
    member_id: str
    track_ids: tuple[str, ...]
    dry_run: bool = True
    preview_run_id: str | None = None


@dataclass(frozen=True, slots=True)
class BackupJob:
    services: tuple[ServiceId, ...] = ()  # () = every set-up service
    trigger: Trigger = Trigger.MANUAL


JobRequest = (
    SyncJob
    | PreviewJob
    | BulkPreviewJob
    | TransferJob
    | BulkTransferJob
    | UndoJob
    | BackupJob
    | CopyToSourceJob
)


@dataclass(frozen=True, slots=True)
class JobProgress:
    phase: str
    done: int
    total: int
    message: str


@dataclass(frozen=True, slots=True)
class Job:
    job_id: str
    request: JobRequest
    status: JobStatus
    created_at: datetime
    label: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    run_ids: tuple[str, ...] = ()
    error: str | None = None
    progress: JobProgress | None = None
    cancel_requested: bool = False
    backup_ids: tuple[str, ...] = ()

    @property
    def kind(self) -> str:
        match self.request:
            case SyncJob():
                return "sync"
            case PreviewJob():
                return "preview"
            case BulkPreviewJob():
                return "bulk_preview"
            case TransferJob():
                return "transfer"
            case BulkTransferJob():
                return "bulk_transfer"
            case UndoJob():
                return "undo"
            case BackupJob():
                return "backup"
            case CopyToSourceJob():
                return "copy_to_source"

    @property
    def sync_id(self) -> str | None:
        if isinstance(self.request, SyncJob | CopyToSourceJob):
            return self.request.sync_id
        return None

    @property
    def finished(self) -> bool:
        return self.status not in ACTIVE_STATUSES


@dataclass(frozen=True, slots=True)
class RunMemberInfo:
    member_id: str
    service: ServiceId
    playlist_id: str | None
    role: MemberRole


@dataclass(frozen=True, slots=True)
class RunContext:
    run_id: str
    members: tuple[RunMemberInfo, ...]
    tracks: tuple[Track, ...]
