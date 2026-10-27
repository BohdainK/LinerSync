import csv
import io
import zipfile
from dataclasses import replace
from datetime import UTC, datetime

from linersync.application.export_csv import (
    csv_filename,
    csv_zip,
    known_ids,
    playlist_csv,
)
from linersync.domain.matching.types import RememberedMatch
from linersync.domain.playlists import PlaylistDraft, PlaylistEntry, PlaylistSnapshot
from linersync.domain.sync import MemberState, SyncedEntry, SyncMode
from linersync.domain.tracks import ServiceId, TrackKey
from tests.factories import NOW, group, snapshot, track

SPOTIFY = ServiceId("spotify")
APPLE = ServiceId("apple_music")


def _rows(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text, newline="")))


def _snap(service: str, *tracks_: object, **kw: object) -> PlaylistSnapshot:
    base = snapshot(service, "P", [])
    entries = tuple(
        t if isinstance(t, PlaylistEntry) else PlaylistEntry(t)  # type: ignore[arg-type]
        for t in tracks_
    )
    return replace(base, entries=entries, **kw)  # type: ignore[arg-type]


def test_columns_and_values() -> None:
    full = track(
        "spotify",
        "a",
        title="One",
        artists=("A", "B"),
        album="Alb",
        isrc="ISRC1",
        duration_ms=205_400,
        url="https://x/a",
    )
    bare = track("spotify", "b", title="Two", duration_ms=None, artists=())
    added = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    snap = _snap("spotify", PlaylistEntry(full, added), bare)
    rows = _rows(playlist_csv(snap, SPOTIFY, {}))
    assert rows[0] == [
        "position",
        "title",
        "artists",
        "album",
        "isrc",
        "duration",
        "date_added",
        "spotify_id",
        "spotify_url",
    ]
    assert rows[1] == [
        "1", "One", "A; B", "Alb", "ISRC1", "3:25", "2026-01-02T03:04:05Z", "a",
        "https://x/a",
    ]  # fmt: skip
    assert rows[2] == ["2", "Two", "", "", "", "", "", "b", ""]


def test_long_duration_has_hours() -> None:
    snap = _snap("spotify", track("spotify", "a", duration_ms=3_725_000))
    assert _rows(playlist_csv(snap, SPOTIFY, {}))[1][5] == "1:02:05"


def test_other_service_ids_from_syncs_and_matches() -> None:
    g = group(("spotify", "P1"), (("apple_music", "A1", SyncMode.FULL),))
    dst = replace(
        g.members[1],
        last_synced=MemberState(
            (SyncedEntry(TrackKey(SPOTIFY, "a"), "A"),),
            PlaylistDraft("x"),
            None,
            NOW,
        ),
    )
    g = replace(g, members=(g.members[0], dst))
    matches = [
        RememberedMatch(TrackKey(SPOTIFY, "b"), APPLE, "B"),
        RememberedMatch(TrackKey(SPOTIFY, "c"), APPLE, None),
    ]
    ids = known_ids([g], matches)
    sp = _snap(
        "spotify",
        track("spotify", "a"),
        track("spotify", "b"),
        track("spotify", "c"),
    )
    rows = _rows(playlist_csv(sp, SPOTIFY, ids))
    assert rows[0][-1] == "apple_music_id"
    assert [r[-1] for r in rows[1:]] == ["A", "B", ""]
    ap = _snap("apple_music", track("apple_music", "A"), track("apple_music", "Z"))
    rows = _rows(playlist_csv(ap, APPLE, ids))
    assert rows[0][-1] == "spotify_id"
    assert [r[-1] for r in rows[1:]] == ["a", ""]


def test_csv_quotes_and_escapes_cells() -> None:
    odd = track("spotify", "a", title='Hello, "World"\nagain', artists=("Beyoncé",))
    f1 = track("spotify", "b", title="=SUM(A1)", artists=("@x",), album="+1")
    snap = _snap("spotify", odd, f1)
    text = playlist_csv(snap, SPOTIFY, {})
    rows = _rows(text)
    assert rows[1][1] == 'Hello, "World"\nagain'
    assert rows[1][2] == "Beyoncé"
    assert rows[2][1:4] == ["'=SUM(A1)", "'@x", "'+1"]
    assert not text.startswith("﻿")
    assert text.split("\r\n")[0].startswith("position,")


def test_repeats_keep_positions() -> None:
    a, b = track("spotify", "a"), track("spotify", "b")
    rows = _rows(playlist_csv(_snap("spotify", a, b, a), SPOTIFY, {}))
    assert [(r[0], r[7]) for r in rows[1:]] == [("1", "a"), ("2", "b"), ("3", "a")]


def test_filename_sanitised() -> None:
    assert csv_filename('a/b: "c"', SPOTIFY) == "a_b_ _c_ (spotify).csv"
    assert csv_filename("", SPOTIFY) == "playlist (spotify).csv"
    assert csv_filename("x" * 300, SPOTIFY) == "x" * 100 + " (spotify).csv"
    assert csv_filename("a\tb\x00  c", SPOTIFY) == "a_b_ c (spotify).csv"


def test_zip_dedupes_names_and_has_bom() -> None:
    data = csv_zip(
        [("Mix (spotify).csv", "x\r\n"), ("Mix (spotify).csv", "y\r\n"),
         ("archive/Mix (spotify).csv", "z\r\n")]
    )  # fmt: skip
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert z.namelist() == [
            "Mix (spotify).csv",
            "Mix (spotify) (2).csv",
            "archive/Mix (spotify).csv",
        ]
        for name in z.namelist():
            assert z.read(name).decode("utf-8").startswith("﻿")


def test_zip_names_are_unique_ignoring_case() -> None:
    data = csv_zip(
        [("Mix (spotify).csv", "x\r\n"), ("mix (spotify).csv", "y\r\n"),
         ("MIX (spotify) (2).csv", "z\r\n")]
    )  # fmt: skip
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert z.namelist() == [
            "Mix (spotify).csv",
            "mix (spotify) (2).csv",
            "MIX (spotify) (2) (2).csv",
        ]
