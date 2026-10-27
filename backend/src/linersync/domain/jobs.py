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


@dataclass(frozen=True, slots=True)
class PreviewJob:
    group: SyncGroup


@dataclass(frozen=True, slots=True)
class TransferJob:
    source_service: ServiceId
    playlist_id: str
    destination: ServiceId
    dry_run: bool = False


@dataclass(frozen=True, slots=True)
class BulkTransferJob:
    transfers: tuple[TransferJob, ...]


@dataclass(frozen=True, slots=True)
class UndoJob:
    run_id: str


@dataclass(frozen=True, slots=True)
class BackupJob:
    services: tuple[ServiceId, ...] = ()  # () = every set-up service
    trigger: Trigger = Trigger.MANUAL


JobRequest = SyncJob | PreviewJob | TransferJob | BulkTransferJob | UndoJob | BackupJob


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
            case TransferJob():
                return "transfer"
            case BulkTransferJob():
                return "bulk_transfer"
            case UndoJob():
                return "undo"
            case BackupJob():
                return "backup"

    @property
    def sync_id(self) -> str | None:
        return self.request.sync_id if isinstance(self.request, SyncJob) else None

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
