from dataclasses import dataclass
from enum import StrEnum

from linersync.domain.tracks import ServiceId, Track, TrackKey


class MatchVia(StrEnum):
    REMEMBERED = "remembered"
    PREVIOUS_SYNC = "previous_sync"
    SAME_SERVICE = "same_service"
    SCORED = "scored"


@dataclass(frozen=True, slots=True)
class ScoredCandidate:
    track: Track
    confidence: float
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Matched:
    target_track_id: str
    confidence: float
    reasons: tuple[str, ...]
    via: MatchVia


@dataclass(frozen=True, slots=True)
class NeedsAttention:
    reasons: tuple[str, ...]
    candidates: tuple[ScoredCandidate, ...]


@dataclass(frozen=True, slots=True)
class NotOnService:
    pass


MatchDecision = Matched | NeedsAttention | NotOnService


@dataclass(frozen=True, slots=True)
class RememberedMatch:
    source_key: TrackKey
    target_service: ServiceId
    target_track_id: str | None  # None = "not on this service"


@dataclass(frozen=True, slots=True)
class MatchConfig:
    accept_threshold: float = 0.85
    min_margin: float = 0.05
    max_candidates: int = 5
    duration_full_s: float = 2.0
    duration_zero_s: float = 15.0
    artwork_max_distance: int = 6
