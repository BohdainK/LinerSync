from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from linersync.domain.matching.types import ScoredCandidate
from linersync.domain.operations import ChangeNotMade
from linersync.domain.tracks import ServiceId, Track


class AttentionKind(StrEnum):
    UNMATCHED = "unmatched"
    CHANGES_NOT_MADE = "changes_not_made"
    BRAKE = "brake"
    SOURCE_MISSING = "source_missing"
    CONFLICT = "conflict"
    SERVICE_FAILING = "service_failing"
    VERIFY_MISMATCH = "verify_mismatch"


@dataclass(frozen=True, slots=True)
class AttentionItem:
    item_id: str
    kind: AttentionKind
    created_at: datetime
    detail: str
    sync_id: str | None = None
    member_id: str | None = None
    service: ServiceId | None = None
    source: Track | None = None
    target_service: ServiceId | None = None
    candidates: tuple[ScoredCandidate, ...] = ()
    not_made: tuple[ChangeNotMade, ...] = ()
    fingerprint: str | None = None
    resolved_at: datetime | None = None
    resolution: str | None = None

    @property
    def dedupe_key(self) -> tuple[str, str, str, str]:
        return (
            str(self.kind),
            self.sync_id or "",
            self.member_id or self.service or "",
            str(self.source.key) if self.source else "",
        )
