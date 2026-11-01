from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal

from linersync.domain.operations import ChangeNotMade, SyncPlan

RunPhase = Literal["read", "match", "plan", "apply", "verify", "done"]


class RunKind(StrEnum):
    SYNC = "sync"
    TRANSFER = "transfer"
    UNDO = "undo"


class Trigger(StrEnum):
    SCHEDULED = "scheduled"
    MANUAL = "manual"


class RunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    ABORTED = "aborted"
    AWAITING_APPROVAL = "awaiting_approval"
    PAUSED_SOURCE_MISSING = "paused_source_missing"
    SKIPPED = "skipped"


class MemberStatus(StrEnum):
    APPLIED = "applied"
    NOTHING_TO_DO = "nothing_to_do"
    DRY_RUN = "dry_run"
    SKIPPED_UNREADABLE = "skipped_unreadable"
    SKIPPED_BREAKER = "skipped_breaker"
    AWAITING_APPROVAL = "awaiting_approval"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class VerificationMismatch:
    member_id: str
    expected: tuple[str, ...]
    actual: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MemberResult:
    member_id: str
    status: MemberStatus
    applied_op_ids: tuple[str, ...] = ()
    not_made: tuple[ChangeNotMade, ...] = ()
    mismatch: VerificationMismatch | None = None
    rebuilt: bool = False
    created_playlist_id: str | None = None
    error: str | None = None
    # The target's track ids as read by a successful verify (the actual list,
    # also on a mismatch); None when there was no verify or its read failed.
    verified_after: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class SyncRun:
    run_id: str
    sync_id: str
    kind: RunKind
    trigger: Trigger
    dry_run: bool
    status: RunStatus
    started_at: datetime
    finished_at: datetime | None = None
    plan: SyncPlan | None = None
    members: tuple[MemberResult, ...] = ()
    error: str | None = None
    undoes_run_id: str | None = None


@dataclass(frozen=True, slots=True)
class RunEvent:
    run_id: str
    sync_id: str
    phase: RunPhase
    done: int
    total: int
    message: str
