from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from linersync.domain.playlists import PlaylistEntry, PlaylistMeta, PlaylistSnapshot
from linersync.domain.sync import (
    Direction,
    MemberRole,
    SyncGroup,
    SyncMember,
    SyncMode,
)
from linersync.domain.tracks import ServiceId, Track

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def track(service: str, track_id: str, **overrides: Any) -> Track:
    fields: dict[str, Any] = {
        "service": ServiceId(service),
        "track_id": track_id,
        "title": track_id,
        "artists": ("Artist",),
        "duration_ms": 200_000,
    }
    fields.update(overrides)
    return Track(**fields)


_UNSET: Any = object()


def snapshot(
    service: str,
    playlist_id: str,
    track_ids: Sequence[str],
    *,
    name: str = "Mix",
    tracks: Mapping[str, Track] | None = None,
    expected_total: int | None = _UNSET,
) -> PlaylistSnapshot:
    known = tracks or {}
    entries = tuple(
        PlaylistEntry(known.get(tid) or track(service, tid)) for tid in track_ids
    )
    return PlaylistSnapshot(
        meta=PlaylistMeta(
            service=ServiceId(service), playlist_id=playlist_id, name=name
        ),
        entries=entries,
        expected_total=len(entries) if expected_total is _UNSET else expected_total,
        taken_at=NOW,
    )


def group(
    source: tuple[str, str | None] = ("spotify", "P1"),
    destinations: Sequence[tuple[str, str | None, SyncMode]] = (
        ("apple_music", "A1", SyncMode.FULL),
    ),
    sync_id: str = "s1",
) -> SyncGroup:
    members = [SyncMember("src", ServiceId(source[0]), source[1], MemberRole.SOURCE)]
    for i, (service, playlist_id, mode) in enumerate(destinations):
        members.append(
            SyncMember(
                f"dst{i}",
                ServiceId(service),
                playlist_id,
                MemberRole.DESTINATION,
                mode,
            )
        )
    return SyncGroup(
        sync_id=sync_id,
        name="Mix",
        direction=Direction.ONE_WAY,
        members=tuple(members),
    )
