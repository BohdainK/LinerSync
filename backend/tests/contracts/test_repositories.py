from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from linersync.adapters.memory.repositories import in_memory_repositories
from linersync.adapters.sqlite.db import connect, migrate
from linersync.adapters.sqlite.repositories import sqlite_repositories
from linersync.domain.attention import AttentionItem, AttentionKind
from linersync.domain.matching.types import RememberedMatch
from linersync.domain.runs import RunKind, RunStatus, SyncRun, Trigger
from linersync.domain.tracks import ServiceId, TrackKey
from linersync.ports.repositories import BreakerState, Repositories
from tests.factories import NOW, group, snapshot, track

Factory = Callable[[], Repositories]


def sqlite_memory_repositories() -> Repositories:
    e = connect(":memory:")
    migrate(e)
    return sqlite_repositories(e)


pytestmark = pytest.mark.parametrize(
    "make", [in_memory_repositories, sqlite_memory_repositories]
)


def _run(run_id: str, sync_id: str, started: datetime) -> SyncRun:
    return SyncRun(
        run_id=run_id,
        sync_id=sync_id,
        kind=RunKind.SYNC,
        trigger=Trigger.MANUAL,
        dry_run=False,
        status=RunStatus.SUCCEEDED,
        started_at=started,
    )


def _item(item_id: str, at: datetime, detail: str = "d") -> AttentionItem:
    return AttentionItem(
        item_id=item_id,
        kind=AttentionKind.UNMATCHED,
        created_at=at,
        detail=detail,
        sync_id="s1",
        member_id="dst0",
        source=track("spotify", "T1"),
    )


def test_sync_roundtrip(make: Factory) -> None:
    repos = make()
    g = group()
    assert repos.syncs.get("s1") is None
    repos.syncs.save(g)
    assert repos.syncs.get("s1") == g
    assert repos.syncs.list() == [g]
    g2 = replace(g, name="Renamed")
    repos.syncs.save(g2)
    assert repos.syncs.list() == [g2]


def test_sync_delete(make: Factory) -> None:
    repos = make()
    g1 = group()
    g2 = replace(g1, sync_id="s2", name="Other")
    repos.syncs.save(g1)
    repos.syncs.save(g2)
    repos.syncs.delete("s1")
    repos.syncs.delete("s1")
    assert repos.syncs.get("s1") is None
    assert repos.syncs.list() == [g2]


def test_list_running(make: Factory) -> None:
    repos = make()
    r1 = replace(_run("r1", "s1", NOW), status=RunStatus.RUNNING)
    r2 = _run("r2", "s1", NOW)
    repos.runs.save(r1)
    repos.runs.save(r2)
    repos.runs.save(replace(r1, status=RunStatus.SUCCEEDED))
    assert repos.runs.list_running() == []
    r3 = replace(_run("r3", "s1", NOW), status=RunStatus.RUNNING)
    repos.runs.save(r3)
    assert repos.runs.list_running() == [r3]


def test_runs_listed_newest_first(make: Factory) -> None:
    repos = make()
    r1 = _run("r1", "s1", NOW)
    r2 = _run("r2", "s1", NOW + timedelta(hours=1))
    r3 = _run("r3", "s2", NOW + timedelta(hours=2))
    for r in (r1, r2, r3):
        repos.runs.save(r)
    assert [r.run_id for r in repos.runs.list_for_sync("s1")] == ["r2", "r1"]
    assert repos.runs.get("r3") == r3
    assert repos.runs.get("nope") is None


def test_runs_tie_break_later_save_first_even_when_resaved(make: Factory) -> None:
    repos = make()
    r1 = _run("r1", "s1", NOW)
    r2 = _run("r2", "s1", NOW)
    repos.runs.save(r1)
    repos.runs.save(replace(r1, status=RunStatus.FAILED))
    repos.runs.save(r2)
    assert [r.run_id for r in repos.runs.list_for_sync("s1")] == ["r2", "r1"]
    repos.runs.save(replace(r1, status=RunStatus.SUCCEEDED))
    assert [r.run_id for r in repos.runs.list_for_sync("s1")] == ["r1", "r2"]


def test_runs_list_for_sync_limit_keeps_newest(make: Factory) -> None:
    repos = make()
    r1 = _run("r1", "s1", NOW)
    r2 = _run("r2", "s1", NOW + timedelta(hours=1))
    r3 = _run("r3", "s1", NOW + timedelta(hours=1))
    r4 = _run("r4", "s2", NOW + timedelta(hours=2))
    for r in (r1, r2, r3, r4):
        repos.runs.save(r)
    assert [r.run_id for r in repos.runs.list_for_sync("s1", limit=1)] == ["r3"]
    assert [r.run_id for r in repos.runs.list_for_sync("s1", limit=2)] == [
        "r3",
        "r2",
    ]
    assert [r.run_id for r in repos.runs.list_for_sync("s1", limit=10)] == [
        "r3",
        "r2",
        "r1",
    ]
    assert [r.run_id for r in repos.runs.list_for_sync("s1", limit=None)] == [
        "r3",
        "r2",
        "r1",
    ]
    assert repos.runs.list_for_sync("nope", limit=1) == []


def test_pre_write_snapshot_roundtrip_including_none(make: Factory) -> None:
    repos = make()
    snap = snapshot("spotify", "P1", ["a", "b"])
    assert not repos.snapshots.has_pre_write("r1", "m1")
    repos.snapshots.save_pre_write("r1", "m1", snap)
    repos.snapshots.save_pre_write("r1", "m2", None)
    assert repos.snapshots.has_pre_write("r1", "m1")
    assert repos.snapshots.get_pre_write("r1", "m1") == snap
    assert repos.snapshots.has_pre_write("r1", "m2")
    assert repos.snapshots.get_pre_write("r1", "m2") is None


def test_remembered_match_roundtrip_including_not_on_service(make: Factory) -> None:
    repos = make()
    key = TrackKey(ServiceId("spotify"), "T1")
    apple = ServiceId("apple_music")
    assert repos.matches.get(key, apple) is None
    repos.matches.remember(RememberedMatch(key, apple, "X9"))
    assert repos.matches.get(key, apple) == RememberedMatch(key, apple, "X9")
    repos.matches.remember(RememberedMatch(key, apple, None))
    got = repos.matches.get(key, apple)
    assert got is not None and got.target_track_id is None
    assert repos.matches.get(key, ServiceId("tidal")) is None


def test_attention_upsert_dedupes_open_items(make: Factory) -> None:
    repos = make()
    first = repos.attention.upsert(_item("i1", NOW))
    second = repos.attention.upsert(_item("i2", NOW + timedelta(days=1), "newer"))
    assert second.item_id == "i1"
    assert second.created_at == NOW
    assert second.detail == "newer"
    assert repos.attention.list_open() == [second]
    assert repos.attention.get("i1") == second
    assert first.item_id == "i1"


def test_attention_resolved_item_allows_new_one(make: Factory) -> None:
    repos = make()
    repos.attention.upsert(_item("i1", NOW))
    later = NOW + timedelta(hours=1)
    repos.attention.resolve("i1", "dismissed", later)
    resolved = repos.attention.get("i1")
    assert resolved is not None
    assert resolved.resolved_at == later and resolved.resolution == "dismissed"
    assert repos.attention.list_open() == []
    new = repos.attention.upsert(_item("i2", later))
    assert new.item_id == "i2"
    assert [i.item_id for i in repos.attention.list_open()] == ["i2"]


def test_breaker_state_defaults_closed(make: Factory) -> None:
    repos = make()
    svc = ServiceId("spotify")
    assert repos.breakers.get(svc) == BreakerState(svc)
    opened = BreakerState(svc, 5, datetime(2026, 1, 1, tzinfo=UTC), "boom")
    repos.breakers.save(opened)
    assert repos.breakers.get(svc) == opened


def test_approval_matches_fingerprint_only(make: Factory) -> None:
    repos = make()
    assert not repos.approvals.is_approved("s1", "m1", "fp1")
    repos.approvals.approve("s1", "m1", "fp1")
    assert repos.approvals.is_approved("s1", "m1", "fp1")
    assert not repos.approvals.is_approved("s1", "m1", "fp2")
    assert not repos.approvals.is_approved("s1", "m2", "fp1")
    assert not repos.approvals.is_approved("s2", "m1", "fp1")


def test_approval_consume_is_single_use(make: Factory) -> None:
    repos = make()
    assert not repos.approvals.consume("s1", "m1", "fp1")
    repos.approvals.approve("s1", "m1", "fp1")
    assert not repos.approvals.consume("s1", "m1", "fp2")
    assert not repos.approvals.consume("s1", "m2", "fp1")
    assert not repos.approvals.consume("s2", "m1", "fp1")
    assert repos.approvals.consume("s1", "m1", "fp1")
    assert not repos.approvals.is_approved("s1", "m1", "fp1")
    assert not repos.approvals.consume("s1", "m1", "fp1")


def test_runs_list_all_oldest_saved_first(make: Factory) -> None:
    repos = make()
    assert repos.runs.list_all() == []
    later = _run("r1", "s1", NOW + timedelta(days=2))
    earlier = _run("r2", "s2", NOW)
    repos.runs.save(later)
    repos.runs.save(earlier)
    assert repos.runs.list_all() == [later, earlier]


def test_matches_list_all(make: Factory) -> None:
    repos = make()
    assert repos.matches.list_all() == []
    apple = ServiceId("apple_music")
    a = RememberedMatch(TrackKey(ServiceId("spotify"), "T1"), apple, "X1")
    b = RememberedMatch(TrackKey(ServiceId("spotify"), "T2"), apple, None)
    repos.matches.remember(a)
    repos.matches.remember(b)
    assert sorted(repos.matches.list_all(), key=lambda m: m.source_key.track_id) == [
        a,
        b,
    ]
