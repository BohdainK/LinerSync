from dataclasses import dataclass, field
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
    # A small album-cover image a browser can load as-is. Display only: not
    # part of the track's identity.
    artwork_url: str | None = field(default=None, compare=False)
    # A search candidate returned for an ISRC query on a service that indexes
    # ISRCs but never returns them. Not part of the track's identity.
    found_by_isrc: bool = field(default=False, compare=False)

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
