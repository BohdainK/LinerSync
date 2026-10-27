from dataclasses import replace
from datetime import timedelta
from typing import Any

from linersync.domain.backups import content_hash
from linersync.domain.playlists import PlaylistEntry, PlaylistSnapshot
from tests.factories import NOW, snapshot


def _with_meta(snap: PlaylistSnapshot, **fields: Any) -> PlaylistSnapshot:
    return replace(snap, meta=replace(snap.meta, **fields))


def test_content_hash_ignores_cover_token_and_time() -> None:
    a = snapshot("spotify", "P1", ["T1", "T2"])
    b = replace(
        _with_meta(a, cover_url="https://x/c.jpg", version_token="v2", track_count=9),
        taken_at=NOW + timedelta(days=3),
    )
    assert content_hash(a) == content_hash(b)


def test_content_hash_sees_order_repeats_name_and_added_at() -> None:
    base = snapshot("spotify", "P1", ["T1", "T2"])
    seen = {content_hash(base)}
    variants = [
        snapshot("spotify", "P1", ["T2", "T1"]),
        snapshot("spotify", "P1", ["T1", "T2", "T1"]),
        snapshot("spotify", "P1", ["T1", "T2"], name="Other"),
        _with_meta(base, description="new"),
        replace(
            base,
            entries=(
                replace(base.entries[0], added_at=NOW),
                base.entries[1],
            ),
        ),
    ]
    for v in variants:
        seen.add(content_hash(v))
    assert len(seen) == 1 + len(variants)
    assert isinstance(base.entries[0], PlaylistEntry)
