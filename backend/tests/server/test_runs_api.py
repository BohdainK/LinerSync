from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from linersync.adapters.memory.runtime import FixedClock
from linersync.domain.jobs import JobStatus
from linersync.domain.playlists import PlaylistMeta
from linersync.domain.tracks import ServiceId
from linersync.ports.errors import ReadFailed
from tests.factories import snapshot, track
from tests.server.conftest import AppFactory, drain, services
from tests.server.helpers import NO_REMOVE, create, run_now, world


def last_run_id(client: TestClient, sync_id: str) -> str:
    run_id: str = client.get(f"/api/syncs/{sync_id}").json()["last_run"]["run_id"]
    return run_id


def get_run(client: TestClient, run_id: str) -> dict[str, Any]:
    response = client.get(f"/api/runs/{run_id}")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def member(run: dict[str, Any], member_id: str) -> dict[str, Any]:
    found: dict[str, Any] = next(
        m for m in run["members"] if m["member_id"] == member_id
    )
    return found


def op(m: dict[str, Any], kind: str) -> dict[str, Any]:
    found: dict[str, Any] = next(o for o in m["operations"] if o["type"] == kind)
    return found


def job(client: TestClient, job_id: str) -> dict[str, Any]:
    response = client.get(f"/api/jobs/{job_id}")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def test_run_detail_names_songs(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        sync_id = create(w.client)["sync_id"]
        run_now(w.client, sync_id)
        drain(w.client)
        run_id = last_run_id(w.client, sync_id)
        run = get_run(w.client, run_id)
    assert run["run_id"] == run_id
    assert (run["kind"], run["added"], run["dry_run"]) == ("sync", 3, False)
    assert run["undoes_run_id"] is None
    assert run["undoable"] is True
    assert run["unresolved"] == []
    dst = member(run, "dst0")
    assert (dst["service"], dst["playlist_id"]) == ("fake_b", "B1")
    assert dst["status"] == "applied"
    assert dst["mismatch"] is None
    add = op(dst, "add_tracks")
    assert [t["title"] for t in add["tracks"]] == ["a", "b", "c"]
    assert [t["track_id"] for t in add["tracks"]] == ["A", "B", "C"]
    assert all(t["known"] and t["service"] == "fake_b" for t in add["tracks"])
    assert add["tracks"][0]["artists"] == ["Artist"]
    assert add["tracks"][0]["duration_ms"] == 200_000
    assert add["positions"] == []
    assert add["order_length"] is None
    assert [m["member_id"] for m in run["members"]] == ["dst0"]
    pairs = [
        (
            p["source"]["service"],
            p["source"]["known"],
            p["target"]["track_id"],
            p["added"],
        )
        for p in dst["matches"]
    ]
    assert pairs == [
        ("fake_a", True, "A", True),
        ("fake_a", True, "B", True),
        ("fake_a", True, "C", True),
    ]


def test_preview_shows_unmatched_with_candidates(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        w.a.add_playlist(
            snapshot(
                "fake_a",
                "P1",
                ["a", "dup"],
                tracks={"dup": track("fake_a", "dup", title="Dup song")},
            )
        )
        w.b.catalog = (
            *w.b.catalog,
            track("fake_b", "D1", title="Dup song"),
            track("fake_b", "D2", title="Dup song"),
        )
        sync_id = create(w.client)["sync_id"]
        run_now(w.client, sync_id, dry_run=True)
        drain(w.client)
        run = get_run(w.client, last_run_id(w.client, sync_id))
    assert run["undoable"] is False
    assert member(run, "dst0")["status"] == "dry_run"
    [entry] = run["unresolved"]
    assert entry["member_id"] == "dst0"
    assert entry["position"] == 1
    assert entry["source"]["title"] == "Dup song"
    assert entry["source"]["known"] is True
    assert isinstance(entry["reasons"], list)
    candidates = entry["candidates"]
    assert sorted(c["track"]["track_id"] for c in candidates) == ["D1", "D2"]
    assert all(c["track"]["title"] == "Dup song" for c in candidates)
    assert all(isinstance(c["confidence"], float) for c in candidates)


def test_dry_run_song_gets_an_attention_item_on_demand(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        w.a.add_playlist(
            snapshot(
                "fake_a",
                "P1",
                ["a", "dup"],
                tracks={"dup": track("fake_a", "dup", title="Dup song")},
            )
        )
        w.b.catalog = (
            *w.b.catalog,
            track("fake_b", "D1", title="Dup song"),
            track("fake_b", "D2", title="Dup song"),
        )
        sync_id = create(w.client)["sync_id"]
        run_now(w.client, sync_id, dry_run=True)
        drain(w.client)
        run_id = last_run_id(w.client, sync_id)
        before = w.client.get("/api/attention").json()
        first = w.client.post(f"/api/runs/{run_id}/unresolved/0/attention", json={})
        second = w.client.post(f"/api/runs/{run_id}/unresolved/0/attention", json={})
        missing = w.client.post(f"/api/runs/{run_id}/unresolved/5/attention", json={})
        assert first.status_code == 200, first.text
        item_id = first.json()["item_id"]
        picked = w.client.post(
            f"/api/attention/{item_id}/pick", json={"target_track_id": "D1"}
        )
    assert before == []
    assert first.status_code == 200, first.text
    item = first.json()
    assert (item["kind"], item["sync_id"], item["target_service"]) == (
        "unmatched",
        sync_id,
        "fake_b",
    )
    assert item["source"]["title"] == "Dup song"
    assert sorted(c["track"]["track_id"] for c in item["candidates"]) == ["D1", "D2"]
    assert second.json()["item_id"] == item_id
    assert missing.status_code == 404
    assert picked.status_code == 204, picked.text


def test_not_made_listed_for_add_only(app_factory: AppFactory) -> None:
    with world(app_factory, b_caps=NO_REMOVE) as w:
        sync_id = create(w.client)["sync_id"]
        run_now(w.client, sync_id)
        drain(w.client)
        w.a.add_playlist(snapshot("fake_a", "P1", ["a", "b"]))
        run_now(w.client, sync_id)
        drain(w.client)
        run = get_run(w.client, last_run_id(w.client, sync_id))
    [change] = member(run, "dst0")["not_made"]
    assert change["kind"] == "remove"
    assert isinstance(change["detail"], str) and change["detail"]
    assert change["track"]["track_id"] == "C"
    assert change["track"]["title"] == "c"
    assert change["track"]["known"] is True


def test_undo_flow(app_factory: AppFactory, clock: FixedClock) -> None:
    with world(app_factory) as w:
        sync_id = create(w.client)["sync_id"]
        run_now(w.client, sync_id)
        drain(w.client)
        run_id = last_run_id(w.client, sync_id)
        assert [e.track.track_id for e in w.b.playlists["B1"].entries] == [
            "A",
            "B",
            "C",
        ]
        response = w.client.post(f"/api/runs/{run_id}/undo", json={})
        assert response.status_code == 202, response.text
        undo_job = response.json()
        assert (undo_job["kind"], undo_job["label"]) == ("undo", "Undo run")
        drain(w.client)
        done = job(w.client, undo_job["job_id"])
        assert done["status"] == "succeeded"
        [undo_run_id] = done["run_ids"]
        undo_run = get_run(w.client, undo_run_id)
        assert undo_run["kind"] == "undo"
        assert undo_run["undoes_run_id"] == run_id
        assert undo_run["undoable"] is False
        assert w.b.playlists["B1"].entries == ()

        clock.advance(timedelta(minutes=1))
        run_now(w.client, sync_id, dry_run=True)
        drain(w.client)
        dry_id = last_run_id(w.client, sync_id)
        refused = w.client.post(f"/api/runs/{dry_id}/undo", json={})
        unknown = w.client.post("/api/runs/nope/undo", json={})
    assert refused.status_code == 409
    assert refused.json() == {"detail": "This run can't be undone"}
    assert unknown.status_code == 404


def test_jobs_active_and_recent(app_factory: AppFactory, clock: FixedClock) -> None:
    with world(app_factory) as w:
        one = create(w.client, name="One")["sync_id"]
        two = create(w.client, name="Two")["sync_id"]
        first = run_now(w.client, one).json()
        clock.advance(timedelta(seconds=1))
        second = run_now(w.client, two).json()
        queued = w.client.get("/api/jobs").json()
        assert [j["job_id"] for j in queued] == [first["job_id"], second["job_id"]]
        assert w.client.get("/api/jobs?state=active").json() == queued
        store = services(w.client).job_store
        stored = store.get(second["job_id"])
        assert stored is not None
        store.save(replace(stored, status=JobStatus.RUNNING))
        active = w.client.get("/api/jobs?state=active").json()
        assert [j["job_id"] for j in active] == [second["job_id"], first["job_id"]]
        assert active[0]["status"] == "running"
        store.save(stored)
        assert w.client.get("/api/jobs?state=recent").json() == []
        drain(w.client)
        recent = w.client.get("/api/jobs?state=recent").json()
        assert w.client.get("/api/jobs").json() == []
        bad = w.client.get("/api/jobs?state=old")
        unknown = w.client.get("/api/jobs/nope")
    assert {j["job_id"] for j in recent} == {first["job_id"], second["job_id"]}
    assert all(j["status"] == "succeeded" for j in recent)
    assert bad.status_code == 422
    assert unknown.status_code == 404


def test_cancel_queued_job(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        sync_id = create(w.client)["sync_id"]
        queued = run_now(w.client, sync_id).json()
        response = w.client.post(f"/api/jobs/{queued['job_id']}/cancel", json={})
        assert response.status_code == 200, response.text
        cancelled = response.json()
        assert cancelled["status"] == "cancelled"
        assert cancelled["finished_at"] is not None
        assert job(w.client, queued["job_id"]) == cancelled
        unknown = w.client.post("/api/jobs/nope/cancel", json={})
        drain(w.client)
    assert unknown.status_code == 404
    assert w.b.playlists["B1"].entries == ()


def test_cancel_finished_job_409(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        sync_id = create(w.client)["sync_id"]
        queued = run_now(w.client, sync_id).json()
        drain(w.client)
        response = w.client.post(f"/api/jobs/{queued['job_id']}/cancel", json={})
        after = job(w.client, queued["job_id"])
    assert response.status_code == 409
    assert response.json() == {"detail": "This job has already finished"}
    assert after["status"] == "succeeded"


def test_transfer_creates_playlist(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        response = w.client.post(
            "/api/transfers",
            json={
                "source_service": "fake_a",
                "playlist_id": "P1",
                "destination": "fake_b",
            },
        )
        assert response.status_code == 202, response.text
        queued = response.json()
        assert (queued["kind"], queued["label"]) == ("transfer", "Transfer to Fake B")
        drain(w.client)
        done = job(w.client, queued["job_id"])
        [run_id] = done["run_ids"]
        run = get_run(w.client, run_id)
        unknown = w.client.post(
            "/api/transfers",
            json={"source_service": "fake_a", "playlist_id": "P1", "destination": "x"},
        )
    assert done["status"] == "succeeded"
    assert run["kind"] == "transfer"
    assert run["undoable"] is True
    dst = member(run, "dst0")
    created = dst["created_playlist_id"]
    assert created is not None and dst["playlist_id"] == created
    create_op = op(dst, "create_playlist")
    assert create_op["name"] == "Mix"
    assert [t["title"] for t in op(dst, "add_tracks")["tracks"]] == ["a", "b", "c"]
    assert [e.track.track_id for e in w.b.playlists[created].entries] == [
        "A",
        "B",
        "C",
    ]
    assert unknown.status_code == 422
    assert unknown.json() == {"detail": "Unknown service x"}


def test_transfer_requires_connected_destination(app_factory: AppFactory) -> None:
    with world(app_factory, connect_b=False) as w:
        response = w.client.post(
            "/api/transfers",
            json={
                "source_service": "fake_a",
                "playlist_id": "P1",
                "destination": "fake_b",
            },
        )
    assert response.status_code == 422
    assert response.json() == {"detail": "Connect Fake B first"}


def test_bulk_transfer_one_job_many_runs(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        w.a.add_playlist(snapshot("fake_a", "P2", ["b"], name="Other"))
        response = w.client.post(
            "/api/transfers/bulk",
            json={
                "items": [
                    {
                        "source_service": "fake_a",
                        "playlist_id": "P1",
                        "destination_services": ["fake_b"],
                    },
                    {
                        "source_service": "fake_a",
                        "playlist_id": "P2",
                        "destination_services": ["fake_b"],
                    },
                ]
            },
        )
        assert response.status_code == 202, response.text
        queued = response.json()
        assert queued["kind"] == "bulk_transfer"
        drain(w.client)
        done = job(w.client, queued["job_id"])
        empty = w.client.post("/api/transfers/bulk", json={"items": []})
        no_dest = w.client.post(
            "/api/transfers/bulk",
            json={
                "items": [
                    {
                        "source_service": "fake_a",
                        "playlist_id": "P1",
                        "destination_services": [],
                    }
                ]
            },
        )
    assert done["status"] == "succeeded"
    assert len(done["run_ids"]) == 2
    assert len([p for p in w.b.playlists if p != "B1"]) == 2
    assert empty.status_code == no_dest.status_code == 422


def test_estimate_counts_and_unreadable(
    app_factory: AppFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    hidden = PlaylistMeta(
        ServiceId("fake_a"),
        "P3",
        "Hidden",
        readable=False,
        unreadable_reason="Private",
    )
    with world(app_factory, b_caps=NO_REMOVE) as w:
        p1 = w.a.playlists["P1"]
        w.a.add_playlist(replace(p1, meta=replace(p1.meta, track_count=3)))
        w.a.add_playlist(replace(p1, meta=hidden, entries=()))

        def body(*playlist_ids: str) -> dict[str, Any]:
            return {
                "items": [
                    {
                        "source_service": "fake_a",
                        "playlist_id": pid,
                        "destination_services": ["fake_b"],
                    }
                    for pid in playlist_ids
                ]
            }

        response = w.client.post("/api/transfers/estimate", json=body("P1", "P3"))
        missing = w.client.post("/api/transfers/estimate", json=body("P9"))

        async def broken() -> list[PlaylistMeta]:
            raise ReadFailed(ServiceId("fake_a"), "Fake A is down")

        monkeypatch.setattr(w.a, "list_playlists", broken)
        failed = w.client.post("/api/transfers/estimate", json=body("P1"))
    assert response.status_code == 200, response.text
    estimate = response.json()
    assert (estimate["playlists"], estimate["songs"]) == (1, 3)
    assert estimate["api_calls"] == {"fake_a": 1, "fake_b": 5}
    assert estimate["add_only_destinations"] == ["fake_b"]
    [unreadable] = estimate["unreadable"]
    assert (unreadable["playlist_id"], unreadable["readable"]) == ("P3", False)
    assert unreadable["unreadable_reason"] == "Private"
    assert missing.status_code == 422
    assert failed.status_code == 502
    assert failed.json() == {"detail": "Fake A is down"}


def test_unknown_run_404(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        response = w.client.get("/api/runs/nope")
    assert response.status_code == 404
    assert response.json() == {"detail": "Unknown run"}


def test_runs_jobs_transfers_require_session(app_factory: AppFactory) -> None:
    with TestClient(app_factory()) as client:
        assert client.get("/api/runs/x").status_code == 401
        assert client.get("/api/jobs").status_code == 401
        assert client.post("/api/transfers/estimate", json={}).status_code == 401
