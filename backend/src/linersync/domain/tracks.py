from dataclasses import dataclass
from typing import NewType

ServiceId = NewType("ServiceId", str)


@dataclass(frozen=True, slots=True)
class TrackKey:
    service: ServiceId
    track_id: str

    def __str__(self) -> str:
        return f"{self.service}:{self.track_id}"


@dataclass(frozen=True, slots=True)
class Track:
    service: ServiceId
    track_id: str
    title: str
    artists: tuple[str, ...]
    album: str | None = None
    duration_ms: int | None = None
    isrc: str | None = None
    artwork_hash: str | None = None
    url: str | None = None

    @property
    def key(self) -> TrackKey:
        return TrackKey(self.service, self.track_id)


@dataclass(frozen=True, slots=True)
class TrackQuery:
    title: str
    artists: tuple[str, ...]
    album: str | None = None
    duration_ms: int | None = None
    isrc: str | None = None

    @classmethod
    def from_track(cls, t: Track) -> "TrackQuery":
        return cls(t.title, t.artists, t.album, t.duration_ms, t.isrc)
