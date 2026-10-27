"""CSV export of backed-up playlists (plan 4 D7)."""

import csv
import io
import re
import zipfile
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime

from linersync.domain.matching.types import RememberedMatch
from linersync.domain.playlists import PlaylistSnapshot
from linersync.domain.sync import SyncGroup
from linersync.domain.tracks import ServiceId, TrackKey

KnownIds = Mapping[TrackKey, Mapping[ServiceId, str]]

FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")
MAX_NAME = 100
_FORBIDDEN = re.compile(r'[/\\:*?"<>|\x00-\x1f\x7f]')


def known_ids(
    syncs: Iterable[SyncGroup], matches: Iterable[RememberedMatch]
) -> dict[TrackKey, dict[ServiceId, str]]:
    """Every song id we know on another service, in both directions."""
    ids: dict[TrackKey, dict[ServiceId, str]] = defaultdict(dict)

    def pair(source: TrackKey, service: ServiceId, track_id: str) -> None:
        ids[source][service] = track_id
        ids[TrackKey(service, track_id)][source.service] = source.track_id

    for group in syncs:
        for member in group.members:
            if member.last_synced is None:
                continue
            for entry in member.last_synced.entries:
                pair(entry.source_key, member.service, entry.target_track_id)
    for match in matches:
        if match.target_track_id is not None:
            pair(match.source_key, match.target_service, match.target_track_id)
    return dict(ids)


def _text(value: str) -> str:
    return "'" + value if value.startswith(FORMULA_STARTS) else value


def _duration(ms: int | None) -> str:
    if ms is None:
        return ""
    total = round(ms / 1000)
    hours, rest = divmod(total, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def playlist_csv(snapshot: PlaylistSnapshot, service: ServiceId, ids: KnownIds) -> str:
    others = sorted(
        {
            other
            for entry in snapshot.entries
            for other in ids.get(entry.track.key, {})
            if other != service
        }
    )
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(
        [
            "position",
            "title",
            "artists",
            "album",
            "isrc",
            "duration",
            "date_added",
            f"{service}_id",
            f"{service}_url",
            *(f"{o}_id" for o in others),
        ]
    )
    for position, entry in enumerate(snapshot.entries, start=1):
        t = entry.track
        added = (
            entry.added_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            if entry.added_at is not None
            else ""
        )
        known = ids.get(t.key, {})
        writer.writerow(
            [
                position,
                _text(t.title),
                _text("; ".join(t.artists)),
                _text(t.album or ""),
                t.isrc or "",
                _duration(t.duration_ms),
                added,
                t.track_id,
                t.url or "",
                *(known.get(o, "") for o in others),
            ]
        )
    return out.getvalue()


def csv_filename(name: str, service: ServiceId) -> str:
    safe = " ".join(_FORBIDDEN.sub("_", name).split())[:MAX_NAME].strip()
    return f"{safe or 'playlist'} ({service}).csv"


def csv_zip(files: Sequence[tuple[str, str]]) -> bytes:
    buffer = io.BytesIO()
    used: set[str] = set()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, text in files:
            candidate, n = path, 1
            stem = path.removesuffix(".csv")
            # Case-insensitive file systems would merge names differing in case.
            while candidate.casefold() in used:
                n += 1
                candidate = f"{stem} ({n}).csv"
            used.add(candidate.casefold())
            archive.writestr(candidate, text.encode("utf-8-sig"))
    return buffer.getvalue()


def zip_stamp(moment: datetime) -> str:
    return moment.strftime("%Y%m%d-%H%M%S")
