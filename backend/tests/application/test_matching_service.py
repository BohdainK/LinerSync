import pytest

from linersync.adapters.fake.connector import FakeConnector
from linersync.adapters.memory.repositories import in_memory_repositories
from linersync.application.matching_service import MatchingFailed, MatchService
from linersync.domain.capabilities import Capability
from linersync.domain.matching.types import (
    MatchConfig,
    Matched,
    MatchVia,
    NeedsAttention,
    NotOnService,
    RememberedMatch,
)
from linersync.domain.playlists import PlaylistDraft
from linersync.domain.sync import MemberState, SyncedEntry
from linersync.domain.tracks import ServiceId, Track
from tests.factories import NOW, snapshot, track

APPLE = ServiceId("apple_music")


def _target(catalog: list[Track] | None = None) -> FakeConnector:
    return FakeConnector(
        APPLE, "Apple Music", frozenset({Capability.ADD}), catalog=catalog or []
    )


def _service() -> tuple[MatchService, FakeConnector]:
    repos = in_memory_repositories()
    return MatchService(repos.matches, MatchConfig()), _target()


def _searches(target: FakeConnector) -> list[str]:
    return [c for c in target.calls if c.startswith("search:")]


async def test_same_service_identity() -> None:
    svc = MatchService(in_memory_repositories().matches, MatchConfig())
    target = FakeConnector(ServiceId("spotify"), "Spotify", frozenset())
    src = snapshot("spotify", "P1", ["a"])
    out = await svc.resolve(src, target, None)
    key = src.entries[0].track.key
    assert out == {key: Matched("a", 1.0, (), MatchVia.SAME_SERVICE)}
    assert target.calls == []


async def test_remembered_beats_search() -> None:
    repos = in_memory_repositories()
    src = snapshot("spotify", "P1", ["a"])
    key = src.entries[0].track.key
    repos.matches.remember(RememberedMatch(key, APPLE, "x9"))
    target = _target([track("apple_music", "a")])
    out = await MatchService(repos.matches, MatchConfig()).resolve(src, target, None)
    assert out[key] == Matched("x9", 1.0, ("your pick",), MatchVia.REMEMBERED)
    assert "search:" not in " ".join(target.calls)


async def test_remembered_not_on_service() -> None:
    repos = in_memory_repositories()
    src = snapshot("spotify", "P1", ["a"])
    key = src.entries[0].track.key
    repos.matches.remember(RememberedMatch(key, APPLE, None))
    target = _target()
    out = await MatchService(repos.matches, MatchConfig()).resolve(src, target, None)
    assert out[key] == NotOnService()
    assert target.calls == []


async def test_previous_pair_reused_without_search() -> None:
    svc, target = _service()
    src = snapshot("spotify", "P1", ["a"])
    key = src.entries[0].track.key
    prev = MemberState((SyncedEntry(key, "p1"),), PlaylistDraft("Mix"), None, NOW)
    out = await svc.resolve(src, target, prev)
    assert out[key] == Matched("p1", 1.0, (), MatchVia.PREVIOUS_SYNC)
    assert target.calls == []


async def test_duplicates_resolved_once() -> None:
    svc, target = _service()
    src = snapshot("spotify", "P1", ["a", "b", "a"])
    out = await svc.resolve(src, target, None)
    assert len(out) == 2
    assert len(_searches(target)) == 2


async def test_search_error_raises_matching_failed() -> None:
    svc, target = _service()
    target.fail_search()
    src = snapshot("spotify", "P1", ["a"])
    with pytest.raises(MatchingFailed) as exc:
        await svc.resolve(src, target, None)
    assert exc.value.service == APPLE
    assert exc.value.__cause__ is not None


async def test_scored_match_via_search() -> None:
    svc, target = _service()
    target.catalog = (track("apple_music", "A-1", title="a"),)
    src = snapshot("spotify", "P1", ["a", "zzz"])
    out = await svc.resolve(src, target, None)
    first, second = (e.track.key for e in src.entries)
    m = out[first]
    assert isinstance(m, Matched)
    assert m.target_track_id == "A-1"
    assert m.via is MatchVia.SCORED
    assert isinstance(out[second], NeedsAttention)
