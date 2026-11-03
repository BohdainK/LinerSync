from datetime import datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from linersync.adapters.memory.runtime import FixedClock
from linersync.domain.capabilities import Capability
from linersync.domain.jobs import JobStatus, SyncJob
from linersync.domain.runs import SyncRun, Trigger
from linersync.domain.tracks import ServiceId, TrackKey
from tests.factories import snapshot, track
from tests.server.conftest import AppFactory, drain, portal, services
from tests.server.helpers import (
    NO_REMOVE,
    archive_p2,
    create,
    new_sync,
    run_now,
    world,
)

DEFAULT_SCHEDULE = "0 */6 * * *"
CANT_REMOVE = "Fake B can't remove songs, so this sync runs as add only into Fake B"


def patch(client: TestClient, sync_id: str, **body: Any) -> Any:
    return client.patch(f"/api/syncs/{sync_id}", json=body)


def delete(client: TestClient, sync_id: str) -> Any:
    return client.request("DELETE", f"/api/syncs/{sync_id}", json={})


def test_create_and_get_sync(app_factory: AppFactory) -> None:
    with world(app_factory, b_caps=NO_REMOVE) as w:
        created = create(w.client)
        sync_id = created["sync_id"]
        fetched = w.client.get(f"/api/syncs/{sync_id}").json()
        listed = w.client.get("/api/syncs").json()
    assert len(sync_id) == 32
    assert created["name"] == "Mix"
    assert created["status"] == "active"
    assert created["pause_reason"] is None
    assert created["schedule"] is None
    assert created["effective_schedule"] == DEFAULT_SCHEDULE
    assert created["source"] == {
        "member_id": "src",
        "service": "fake_a",
        "playlist_id": "P1",
    }
    assert created["destinations"] == [
        {
            "member_id": "dst0",
            "service": "fake_b",
            "playlist_id": "B1",
            "mode": "full",
            "effective_mode": "add_only",
            "limitations": [CANT_REMOVE],
            "last_synced_at": None,
            "missing": False,
            "removed_by_you": [],
            "added_by_you": [],
            "can_copy_to_source": True,
        }
    ]
    assert created["active_job"] is None
    assert created["last_run"] is None
    assert fetched == created
    assert listed == [created]


def test_list_sorted_by_name_and_unknown_is_404(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        create(w.client, name="beta")
        create(w.client, name="Alpha")
        names = [s["name"] for s in w.client.get("/api/syncs").json()]
        assert w.client.get("/api/syncs/nope").status_code == 404
        assert patch(w.client, "nope", name="x").status_code == 404
        assert delete(w.client, "nope").status_code == 404
        assert run_now(w.client, "nope").status_code == 404
        assert w.client.get("/api/syncs/nope/runs").status_code == 404
    assert names == ["Alpha", "beta"]


def test_syncs_require_session(app_factory: AppFactory) -> None:
    with TestClient(app_factory()) as client:
        assert client.get("/api/syncs").status_code == 401
        assert client.post("/api/syncs", json=new_sync()).status_code == 401


def test_create_requires_connected_services(app_factory: AppFactory) -> None:
    with world(app_factory, connect_b=False) as w:
        response = w.client.post("/api/syncs", json=new_sync())
        assert response.status_code == 422
        assert response.json() == {"detail": "Connect Fake B first"}
        unknown = new_sync(destinations=[{"service": "nope", "playlist_id": "X"}])
        response = w.client.post("/api/syncs", json=unknown)
        assert response.status_code == 422
        assert response.json() == {"detail": "Unknown service nope"}
        assert w.client.get("/api/syncs").json() == []


def test_invalid_schedule_rejected(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        response = w.client.post("/api/syncs", json=new_sync(schedule="every day"))
        assert response.status_code == 422
        assert isinstance(response.json()["detail"], str)
        assert w.client.get("/api/syncs").json() == []
        sync_id = create(w.client)["sync_id"]
        response = patch(w.client, sync_id, schedule="99 * * * *")
        assert response.status_code == 422
        assert isinstance(response.json()["detail"], str)


def test_destination_same_as_source_rejected(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        body = new_sync(destinations=[{"service": "fake_a", "playlist_id": "P1"}])
        response = w.client.post("/api/syncs", json=body)
    assert response.status_code == 422
    assert response.json() == {"detail": "a destination cannot be the source playlist"}


def test_patch_name_schedule_modes_and_destinations(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        sync_id = create(w.client)["sync_id"]
        response = patch(
            w.client,
            sync_id,
            name="  New  ",
            schedule="0 3 * * *",
            modes={"dst0": "add_only"},
            add_destinations=[{"service": "fake_a", "playlist_id": "P2"}],
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["name"] == "New"
        assert body["schedule"] == "0 3 * * *"
        assert body["effective_schedule"] == "0 3 * * *"
        dests = {d["member_id"]: d for d in body["destinations"]}
        assert dests["dst0"]["mode"] == "add_only"
        assert dests["dst0"]["effective_mode"] == "add_only"
        assert (dests["dst1"]["service"], dests["dst1"]["playlist_id"]) == (
            "fake_a",
            "P2",
        )
        assert dests["dst1"]["mode"] == "full"

        removed = patch(w.client, sync_id, remove_member_ids=["dst0"]).json()
        assert [d["member_id"] for d in removed["destinations"]] == ["dst1"]
        added = patch(
            w.client, sync_id, add_destinations=[{"service": "fake_b"}]
        ).json()
        ids = sorted(d["member_id"] for d in added["destinations"])
        assert ids == ["dst0", "dst1"]

        paused = patch(w.client, sync_id, paused=True).json()
        assert (paused["status"], paused["pause_reason"]) == ("paused", "Paused by you")
        resumed = patch(w.client, sync_id, paused=False).json()
        assert (resumed["status"], resumed["pause_reason"]) == ("active", None)

        no_src = patch(w.client, sync_id, remove_member_ids=["src"])
        assert no_src.status_code == 422
        unknown_mode = patch(w.client, sync_id, modes={"dst9": "full"})
        assert unknown_mode.status_code == 422
        none_left = patch(w.client, sync_id, remove_member_ids=["dst0", "dst1"])
        assert none_left.status_code == 422
        unset_up = patch(w.client, sync_id, add_destinations=[{"service": "nope"}])
        assert unset_up.json() == {"detail": "Unknown service nope"}
        final = w.client.get(f"/api/syncs/{sync_id}").json()
    assert final == resumed


def test_patch_schedule_null_resets_to_default(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        sync_id = create(w.client, schedule="0 3 * * *")["sync_id"]
        renamed = patch(w.client, sync_id, name="Other").json()
        assert renamed["schedule"] == "0 3 * * *"
        reset = patch(w.client, sync_id, schedule=None).json()
    assert reset["schedule"] is None
    assert reset["effective_schedule"] == DEFAULT_SCHEDULE


def test_patch_while_job_active_409(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        sync_id = create(w.client)["sync_id"]
        assert run_now(w.client, sync_id).status_code == 202
        response = patch(w.client, sync_id, name="Other")
        assert response.status_code == 409
        assert response.json() == {"detail": "Wait for the current run to finish"}
        drain(w.client)
        assert patch(w.client, sync_id, name="Other").status_code == 200


def test_delete_while_job_active_409(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        sync_id = create(w.client)["sync_id"]
        assert run_now(w.client, sync_id).status_code == 202
        response = delete(w.client, sync_id)
        assert response.status_code == 409
        assert response.json() == {"detail": "Wait for the current run to finish"}
        drain(w.client)
        assert delete(w.client, sync_id).status_code == 204
        assert w.client.get(f"/api/syncs/{sync_id}").status_code == 404
        assert w.client.get("/api/syncs").json() == []


def test_delete_keeps_run_history(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        sync_id = create(w.client)["sync_id"]
        assert run_now(w.client, sync_id).status_code == 202
        drain(w.client)
        run_id = w.client.get(f"/api/syncs/{sync_id}").json()["last_run"]["run_id"]
        assert delete(w.client, sync_id).status_code == 204
        assert w.client.get(f"/api/runs/{run_id}").status_code == 200


def test_run_now_returns_job_and_dedupes(
    app_factory: AppFactory, clock: FixedClock
) -> None:
    with world(app_factory) as w:
        sync_id = create(w.client)["sync_id"]
        first = run_now(w.client, sync_id)
        second = run_now(w.client, sync_id)
        assert first.status_code == second.status_code == 202
        job = first.json()
        assert second.json() == job
        assert job["kind"] == "sync"
        assert job["status"] == "queued"
        assert job["label"] == "Sync “Mix”"
        assert job["sync_id"] == sync_id
        assert job["run_ids"] == []
        assert job["progress"] is None
        assert job["cancel_requested"] is False
        active = w.client.get(f"/api/syncs/{sync_id}").json()["active_job"]
        assert active == job
        clock.advance(timedelta(seconds=1))  # FIFO order is by created_at
        dry = run_now(w.client, sync_id, dry_run=True).json()
        assert dry["job_id"] != job["job_id"]
        assert dry["label"] == "Dry run “Mix”"
        drain(w.client)
        after = w.client.get(f"/api/syncs/{sync_id}").json()
        stored = services(w.client).job_store.get(job["job_id"])
    assert stored is not None and stored.status is JobStatus.SUCCEEDED
    assert stored.request == SyncJob(sync_id, Trigger.MANUAL)
    assert after["active_job"] is None
    assert after["last_run"]["dry_run"] is True
    assert after["destinations"][0]["last_synced_at"] is not None
    assert [e.track.track_id for e in w.b.playlists["B1"].entries] == ["A", "B", "C"]


def test_preview_writes_nothing_and_returns_dry_run(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        response = w.client.post("/api/syncs/preview", json=new_sync())
        assert response.status_code == 202
        job = response.json()
        assert job["kind"] == "preview"
        assert job["label"] == "Preview “Mix”"
        drain(w.client)
        svc = services(w.client)
        stored = svc.job_store.get(job["job_id"])
        assert stored is not None and stored.status is JobStatus.SUCCEEDED
        run = svc.repos.runs.get(stored.run_ids[0])
        assert w.client.get("/api/syncs").json() == []
        assert svc.repos.syncs.list() == []
        bad = w.client.post("/api/syncs/preview", json=new_sync(schedule="nope"))
        assert bad.status_code == 422
    assert run is not None and run.dry_run
    assert run.sync_id.startswith("preview-")
    assert w.b.playlists["B1"].entries == ()
    assert not [c for c in w.b.calls if c.startswith(("apply", "create"))]


def test_runs_listed_newest_first_with_counts(
    app_factory: AppFactory, clock: FixedClock
) -> None:
    with world(app_factory, source=("a", "b", "c", "zzz")) as w:
        sync_id = create(w.client)["sync_id"]
        run_now(w.client, sync_id, dry_run=True)
        drain(w.client)
        clock.advance(timedelta(minutes=1))
        run_now(w.client, sync_id)
        drain(w.client)
        runs = w.client.get(f"/api/syncs/{sync_id}/runs").json()
        limited = w.client.get(f"/api/syncs/{sync_id}/runs?limit=1").json()
        too_small = w.client.get(f"/api/syncs/{sync_id}/runs?limit=0")
        too_big = w.client.get(f"/api/syncs/{sync_id}/runs?limit=201")
        last = w.client.get(f"/api/syncs/{sync_id}").json()["last_run"]
    assert [r["dry_run"] for r in runs] == [False, True]
    real, dry = runs
    assert real["sync_id"] == sync_id
    assert (real["kind"], real["trigger"]) == ("sync", "manual")
    assert real["error"] is None
    assert real["finished_at"] is not None
    assert (real["added"], real["removed"], real["unmatched"]) == (3, 0, 1)
    assert (dry["added"], dry["removed"], dry["unmatched"]) == (3, 0, 1)
    assert datetime.fromisoformat(real["started_at"]) > datetime.fromisoformat(
        dry["started_at"]
    )
    assert limited == [real]
    assert last == real
    assert too_small.status_code == too_big.status_code == 422


def test_sync_views_read_a_bounded_run_history(
    app_factory: AppFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    with world(app_factory) as w:
        sync_id = create(w.client)["sync_id"]
        runs = services(w.client).repos.runs
        real = runs.list_for_sync
        limits: list[int | None] = []

        def spy(sync_id: str, limit: int | None = None) -> list[SyncRun]:
            limits.append(limit)
            return real(sync_id, limit=limit)

        monkeypatch.setattr(runs, "list_for_sync", spy)
        w.client.get("/api/syncs")
        w.client.get(f"/api/syncs/{sync_id}")
        w.client.get(f"/api/syncs/{sync_id}/runs?limit=7")
    assert limits == [10, 10, 7]


def test_bulk_create_all_or_nothing(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        ok = w.client.post(
            "/api/syncs/bulk", json={"syncs": [new_sync("One"), new_sync("Two")]}
        )
        assert ok.status_code == 201
        assert [s["name"] for s in ok.json()] == ["One", "Two"]
        bad = w.client.post(
            "/api/syncs/bulk",
            json={
                "syncs": [
                    new_sync("Three"),
                    new_sync(
                        "Four", destinations=[{"service": "nope", "playlist_id": "X"}]
                    ),
                ]
            },
        )
        assert bad.status_code == 422
        assert bad.json() == {"detail": "Unknown service nope"}
        names = [s["name"] for s in w.client.get("/api/syncs").json()]
    assert names == ["One", "Two"]


def test_next_run_at_reflects_schedule(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        custom = create(w.client, name="Custom", schedule="30 3 * * *")
        default = create(w.client, name="Default")
        paused = patch(w.client, default["sync_id"], paused=True).json()
        resumed = patch(w.client, default["sync_id"], paused=False).json()
    at = datetime.fromisoformat(custom["next_run_at"])
    assert (at.hour, at.minute) == (3, 30)
    assert at.utcoffset() == timedelta(0)
    default_at = datetime.fromisoformat(default["next_run_at"])
    assert default_at.hour % 6 == 0 and default_at.minute == 0
    assert paused["next_run_at"] is None
    assert resumed["next_run_at"] is not None


def test_due_schedule_submits_scheduled_job(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        sync_id = create(w.client)["sync_id"]
        svc = services(w.client)
        aps_job = svc.scheduler.aps.get_job(f"sync:{sync_id}")
        assert aps_job is not None
        portal(w.client).call(aps_job.func)
        active = w.client.get(f"/api/syncs/{sync_id}").json()["active_job"]
        assert active["label"] == "Sync “Mix”"
        job = svc.job_store.get(active["job_id"])
        assert job is not None
        assert job.request == SyncJob(sync_id, Trigger.SCHEDULED)
        drain(w.client)
        svc.repos.syncs.delete(sync_id)
        portal(w.client).call(aps_job.func)
        assert svc.jobs.active_for_sync(sync_id) is None


def test_missing_after_dry_run_rebuild(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        sync_id = create(w.client)["sync_id"]
        run_now(w.client, sync_id)
        drain(w.client)
        w.b.delete_playlist("B1")
        run_now(w.client, sync_id, dry_run=True)
        drain(w.client)
        missing = w.client.get(f"/api/syncs/{sync_id}").json()["destinations"][0]
        run_now(w.client, sync_id)
        drain(w.client)
        rebuilt = w.client.get(f"/api/syncs/{sync_id}").json()["destinations"][0]
    assert missing["missing"] is True
    assert rebuilt["missing"] is False
    assert rebuilt["playlist_id"] != "B1"


def test_sync_from_archived_playlist(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        entry = archive_p2(w)
        body = new_sync(name="Archive")
        body["source"] = {"service": "backup", "playlist_id": entry["entry_id"]}
        response = w.client.post("/api/syncs", json=body)
        assert response.status_code == 201, response.text
        created = response.json()
        assert created["source"]["service"] == "backup"
        assert run_now(w.client, created["sync_id"]).status_code == 202
        drain(w.client)
        assert list(w.b.playlists["B1"].track_ids) == ["A", "B"]
        preview = w.client.post("/api/syncs/preview", json=body)
        assert preview.status_code == 202
        response = w.client.post("/api/syncs/bulk", json={"syncs": [body]})
        assert response.status_code == 201, response.text


def test_backup_as_destination_rejected(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        entry = archive_p2(w)
        body = new_sync(destinations=[{"service": "backup", "playlist_id": "x"}])
        response = w.client.post("/api/syncs", json=body)
        assert response.status_code == 422
        assert response.json() == {"detail": "Unknown service backup"}
        body["source"] = {"service": "backup", "playlist_id": entry["entry_id"]}
        response = w.client.post("/api/syncs", json=body)
        assert response.status_code == 422
        assert response.json() == {"detail": "Unknown service backup"}


def test_unknown_backup_source_rejected(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        body = new_sync()
        body["source"] = {"service": "backup", "playlist_id": "nope"}
        for url in ("/api/syncs", "/api/syncs/preview"):
            response = w.client.post(url, json=body)
            assert response.status_code == 422
            assert response.json() == {"detail": "Unknown backup"}
        response = w.client.post("/api/syncs/bulk", json={"syncs": [body]})
        assert response.status_code == 422
        assert response.json() == {"detail": "Unknown backup"}
        assert w.client.get("/api/syncs").json() == []


# -- start from a preview -----------------------------------------------------
def _preview_run(w: Any, body: dict[str, Any]) -> str:
    response = w.client.post("/api/syncs/preview", json=body)
    assert response.status_code == 202, response.text
    drain(w.client)
    job = w.client.get(f"/api/jobs/{response.json()['job_id']}").json()
    run_id: str = job["run_ids"][0]
    return run_id


def _pick_b9() -> dict[str, str]:
    return {
        "source_service": "fake_a",
        "source_track_id": "b",
        "target_service": "fake_b",
        "target_track_id": "B9",
    }


def test_create_with_start_runs_first_sync_from_preview(
    app_factory: AppFactory,
) -> None:
    with world(app_factory) as w:
        w.b.catalog = (*w.b.catalog, track("fake_b", "B9", title="b nine"))
        body = new_sync()
        run_id = _preview_run(w, body)
        body["start"] = {"preview_run_id": run_id, "picks": [_pick_b9()]}
        response = w.client.post("/api/syncs", json=body)
        assert response.status_code == 201, response.text
        out = response.json()
        drain(w.client)
        job = w.client.get(f"/api/jobs/{out['active_job']['job_id']}").json()
        ids = list(w.b.playlists["B1"].track_ids)
    assert job["kind"] == "sync" and job["status"] == "succeeded"
    assert ids == ["A", "B9", "C"]


def _rejects_start(w: Any, body: dict[str, Any], url: str = "/api/syncs") -> None:
    before = w.client.get("/api/syncs").json()
    response = w.client.post(url, json=body)
    assert response.status_code == 422, response.text
    assert w.client.get("/api/syncs").json() == before
    assert (
        services(w.client).repos.matches.get(
            TrackKey(ServiceId("fake_a"), "b"), ServiceId("fake_b")
        )
        is None
    )


def test_create_with_start_rejects_preview_of_other_source(
    app_factory: AppFactory,
) -> None:
    with world(app_factory) as w:
        other = new_sync()
        other["source"] = {"service": "fake_a", "playlist_id": "P2"}
        run_id = _preview_run(w, other)
        body = {
            **new_sync(),
            "start": {"preview_run_id": run_id, "picks": [_pick_b9()]},
        }
        _rejects_start(w, body)


def test_create_with_start_rejects_unknown_or_real_run(
    app_factory: AppFactory,
) -> None:
    with world(app_factory) as w:
        unknown = {
            **new_sync(),
            "start": {"preview_run_id": "nope", "picks": [_pick_b9()]},
        }
        _rejects_start(w, unknown)
        created = create(w.client, name="Real")
        run_now(w.client, created["sync_id"])
        drain(w.client)
        [real] = services(w.client).repos.runs.list_for_sync(created["sync_id"])
        assert not real.dry_run
        body = {
            **new_sync(),
            "start": {"preview_run_id": real.run_id, "picks": [_pick_b9()]},
        }
        _rejects_start(w, body)


def test_bulk_create_with_bad_start_saves_nothing(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        good = new_sync()
        good["start"] = {"preview_run_id": _preview_run(w, new_sync()), "picks": []}
        bad = {
            **new_sync(name="Bad"),
            "start": {"preview_run_id": "nope", "picks": [_pick_b9()]},
        }
        _rejects_start(w, {"syncs": [good, bad]}, "/api/syncs/bulk")


def test_bulk_create_with_start_runs_each(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        body = new_sync()
        body["start"] = {"preview_run_id": _preview_run(w, body), "picks": []}
        response = w.client.post("/api/syncs/bulk", json={"syncs": [body]})
        assert response.status_code == 201, response.text
        [out] = response.json()
        drain(w.client)
        job = w.client.get(f"/api/jobs/{out['active_job']['job_id']}").json()
    assert job["status"] == "succeeded"
    assert list(w.b.playlists["B1"].track_ids) == ["A", "B", "C"]


def test_bulk_preview_returns_one_run_per_sync_in_order(
    app_factory: AppFactory,
) -> None:
    with world(app_factory) as w:
        second = new_sync(name="Second")
        second["source"] = {"service": "fake_a", "playlist_id": "P2"}
        response = w.client.post(
            "/api/syncs/preview/bulk", json={"syncs": [new_sync(), second]}
        )
        assert response.status_code == 202, response.text
        queued = response.json()
        drain(w.client)
        job = w.client.get(f"/api/jobs/{queued['job_id']}").json()
        svc = services(w.client)
        runs = [svc.repos.runs.get(r) for r in job["run_ids"]]
        listed = w.client.get("/api/syncs").json()
    assert (queued["kind"], queued["label"]) == ("bulk_preview", "Preview 2 syncs")
    assert len(runs) == 2 and all(r is not None and r.dry_run for r in runs)
    assert [r.sync_id.startswith("preview-") for r in runs if r] == [True, True]
    assert [len(r.plan.member_plans) if r and r.plan else 0 for r in runs][0] > 0
    assert listed == []


# -- merge or overwrite, user edits and re-add --------------------------------
def _member(w: Any, sync_id: str) -> Any:
    group = services(w.client).repos.syncs.get(sync_id)
    assert group is not None
    return group.destinations[0]


def _destination_body(**extra: Any) -> list[dict[str, Any]]:
    return [{"service": "fake_b", "playlist_id": "B1", **extra}]


def test_overwrite_sets_mirror_once(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        body = new_sync(destinations=_destination_body(existing="overwrite"))
        sync_id = w.client.post("/api/syncs", json=body).json()["sync_id"]
        new = new_sync(destinations=[{"service": "fake_b", "existing": "overwrite"}])
        created = w.client.post("/api/syncs", json=new).json()["sync_id"]
        overwrite = _member(w, sync_id).mirror_once
        fresh = _member(w, created).mirror_once
    assert overwrite is True
    assert fresh is False  # a new playlist has nothing to overwrite


def test_merge_is_default(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        sync_id = create(w.client)["sync_id"]
        merge = w.client.post(
            "/api/syncs",
            json=new_sync(destinations=_destination_body(existing="merge")),
        ).json()["sync_id"]
        defaults = _member(w, sync_id).mirror_once
        explicit = _member(w, merge).mirror_once
    assert defaults is False and explicit is False


def test_preview_with_overwrite_plans_a_strict_mirror(
    app_factory: AppFactory,
) -> None:
    with world(app_factory) as w:
        w.b.add_playlist(snapshot("fake_b", "B1", ["X", "A"]))
        body = new_sync(destinations=_destination_body(existing="overwrite"))
        run_id = _preview_run(w, body)
        run = services(w.client).repos.runs.get(run_id)
        merge_run = services(w.client).repos.runs.get(_preview_run(w, new_sync()))
    assert run is not None and merge_run is not None
    assert run.plan is not None and merge_run.plan is not None
    assert run.plan.for_member("dst0").expected_after == ("A", "B", "C")
    assert "X" in merge_run.plan.for_member("dst0").expected_after


def _synced_then_deleted(w: Any) -> str:
    """A synced Mix whose destination copy of "b" the user then deleted."""
    sync_id: str = create(w.client)["sync_id"]
    run_now(w.client, sync_id)
    drain(w.client)
    w.b.add_playlist(snapshot("fake_b", "B1", ["A", "C"]))
    run_now(w.client, sync_id)
    drain(w.client)
    return sync_id


def test_sync_out_lists_user_edits(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        sync_id = _synced_then_deleted(w)
        w.b.add_playlist(snapshot("fake_b", "B1", ["A", "C", "Z"]))
        run_now(w.client, sync_id)
        drain(w.client)
        out = w.client.get(f"/api/syncs/{sync_id}").json()["destinations"][0]
    assert [t["track_id"] for t in out["removed_by_you"]] == ["b"]
    assert out["removed_by_you"][0]["service"] == "fake_a"
    assert [t["track_id"] for t in out["added_by_you"]] == ["Z"]
    assert out["can_copy_to_source"] is True


def test_re_add_runs_sync_and_puts_song_back(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        sync_id = _synced_then_deleted(w)
        assert list(w.b.playlists["B1"].track_ids) == ["A", "C"]
        response = w.client.post(
            f"/api/syncs/{sync_id}/destinations/dst0/re-add",
            json={"source_service": "fake_a", "source_track_id": "b"},
        )
        assert response.status_code == 202, response.text
        assert response.json()["label"] == "Sync “Mix”"
        busy = w.client.post(
            f"/api/syncs/{sync_id}/destinations/dst0/re-add",
            json={"source_service": "fake_a", "source_track_id": "b"},
        )
        drain(w.client)
        out = w.client.get(f"/api/syncs/{sync_id}").json()["destinations"][0]
        ids = sorted(w.b.playlists["B1"].track_ids)
    assert busy.status_code == 409
    assert ids == ["A", "B", "C"]
    assert out["removed_by_you"] == []


def test_re_add_restores_one_of_a_repeated_song(app_factory: AppFactory) -> None:
    with world(app_factory, source=("a", "a", "b")) as w:
        sync_id: str = create(w.client)["sync_id"]
        run_now(w.client, sync_id)
        drain(w.client)
        assert list(w.b.playlists["B1"].track_ids) == ["A", "A", "B"]
        w.b.add_playlist(snapshot("fake_b", "B1", ["A", "B"]))
        run_now(w.client, sync_id)
        drain(w.client)
        removed = w.client.get(f"/api/syncs/{sync_id}").json()["destinations"][0]
        assert [t["track_id"] for t in removed["removed_by_you"]] == ["a"]
        response = w.client.post(
            f"/api/syncs/{sync_id}/destinations/dst0/re-add",
            json={"source_service": "fake_a", "source_track_id": "a"},
        )
        assert response.status_code == 202, response.text
        drain(w.client)
        out = w.client.get(f"/api/syncs/{sync_id}").json()["destinations"][0]
        ids = list(w.b.playlists["B1"].track_ids)
    assert ids == ["A", "A", "B"]
    assert out["removed_by_you"] == []


def test_re_add_unknown_song_422(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        sync_id = _synced_then_deleted(w)
        url = f"/api/syncs/{sync_id}/destinations/dst0/re-add"
        song = {"source_service": "fake_a", "source_track_id": "nope"}
        bad = w.client.post(url, json=song)
        no_sync = w.client.post("/api/syncs/nope/destinations/dst0/re-add", json=song)
        no_member = w.client.post(
            f"/api/syncs/{sync_id}/destinations/dst9/re-add", json=song
        )
    assert bad.status_code == 422
    assert no_sync.status_code == 404
    assert no_member.status_code == 404


def test_cannot_copy_to_source_without_add(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        sync_id = create(w.client)["sync_id"]
        before = w.client.get(f"/api/syncs/{sync_id}").json()["destinations"][0]
        assert before["can_copy_to_source"] is True
        plugin = services(w.client).registry.plugins[ServiceId("fake_a")]
        plugin.capabilities = plugin.capabilities - {Capability.ADD}
        out = w.client.get(f"/api/syncs/{sync_id}").json()["destinations"][0]
    assert out["can_copy_to_source"] is False


# -- copy to source -----------------------------------------------------------
def _user_added_z(w: Any) -> str:
    """A synced Mix where the user then added Z ("z") on B1; Fake A has "z"."""
    w.a.catalog = (*w.a.catalog, track("fake_a", "z", title="z"))
    sync_id: str = create(w.client)["sync_id"]
    run_now(w.client, sync_id)
    drain(w.client)
    z = track("fake_b", "Z", title="z")
    w.b.add_playlist(snapshot("fake_b", "B1", ["A", "B", "C", "Z"], tracks={"Z": z}))
    run_now(w.client, sync_id)
    drain(w.client)
    return sync_id


def _copy_url(sync_id: str, member_id: str = "dst0") -> str:
    return f"/api/syncs/{sync_id}/destinations/{member_id}/copy-to-source"


def test_copy_to_source_previews_then_copies(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        sync_id = _user_added_z(w)
        preview = w.client.post(_copy_url(sync_id), json={"track_ids": ["Z"]})
        assert preview.status_code == 202, preview.text
        busy = w.client.get(f"/api/syncs/{sync_id}").json()["active_job"]
        drain(w.client)
        preview_job = w.client.get(f"/api/jobs/{preview.json()['job_id']}").json()
        after_preview = w.a.playlists["P1"].track_ids
        real = w.client.post(
            _copy_url(sync_id),
            json={"dry_run": False, "preview_job_id": preview_job["job_id"]},
        )
        assert real.status_code == 202, real.text
        drain(w.client)
        job = w.client.get(f"/api/jobs/{real.json()['job_id']}").json()
        runs = [services(w.client).repos.runs.get(r) for r in job["run_ids"]]
        out = w.client.get(f"/api/syncs/{sync_id}").json()["destinations"][0]
    assert preview.json()["kind"] == "copy_to_source"
    assert preview.json()["sync_id"] == sync_id
    assert busy is not None and busy["job_id"] == preview.json()["job_id"]
    assert preview_job["status"] == "succeeded"
    assert len(preview_job["run_ids"]) == 1
    assert after_preview == ("a", "b", "c")
    assert job["status"] == "succeeded", job
    assert [(r.kind, r.dry_run) for r in runs if r is not None] == [
        ("copy_to_source", False),
        ("sync", False),
    ]
    assert w.a.playlists["P1"].track_ids == ("a", "b", "c", "z")
    assert w.b.playlists["B1"].track_ids == ("A", "B", "C", "Z")
    assert out["added_by_you"] == []


def test_copy_to_source_requires_preview_for_real_copy(
    app_factory: AppFactory,
) -> None:
    with world(app_factory) as w:
        sync_id = _user_added_z(w)
        bare = w.client.post(_copy_url(sync_id), json={"dry_run": False})
        sync_job = run_now(w.client, sync_id, dry_run=True).json()["job_id"]
        drain(w.client)
        not_a_copy = w.client.post(
            _copy_url(sync_id), json={"dry_run": False, "preview_job_id": sync_job}
        )
        unknown = w.client.post(
            _copy_url(sync_id), json={"dry_run": False, "preview_job_id": "nope"}
        )
        drain(w.client)
        w.b.add_playlist(snapshot("fake_b", "B2", ["Z"]))  # merged: the user's
        patch(
            w.client,
            sync_id,
            add_destinations=[{"service": "fake_b", "playlist_id": "B2"}],
        )
        run_now(w.client, sync_id)
        drain(w.client)
        other = w.client.post(_copy_url(sync_id, "dst1"), json={}).json()["job_id"]
        drain(w.client)
        wrong_member = w.client.post(
            _copy_url(sync_id), json={"dry_run": False, "preview_job_id": other}
        )
        copied = w.a.playlists["P1"].track_ids
    assert bare.status_code == 422
    assert not_a_copy.status_code == 422
    assert unknown.status_code == 422
    assert wrong_member.status_code == 422
    assert copied == ("a", "b", "c")


def test_copy_to_source_refused(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        sync_id = _user_added_z(w)
        not_added = w.client.post(_copy_url(sync_id), json={"track_ids": ["A"]})
        no_member = w.client.post(_copy_url(sync_id, "dst9"), json={})
        no_sync = w.client.post(_copy_url("nope"), json={})
        plugin = services(w.client).registry.plugins[ServiceId("fake_a")]
        plugin.capabilities = plugin.capabilities - {Capability.ADD}
        cant_add = w.client.post(_copy_url(sync_id), json={})
    assert not_added.status_code == 422
    assert no_member.status_code == 404
    assert no_sync.status_code == 404
    assert cant_add.status_code == 422


def test_copy_to_source_applies_the_picks(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        sync_id = _user_added_z(w)
        w.a.catalog = (
            track("fake_a", "z1", title="z"),
            track("fake_a", "z2", title="z"),
        )
        preview = w.client.post(_copy_url(sync_id), json={}).json()["job_id"]
        drain(w.client)
        pick = {
            "source_service": "fake_b",
            "source_track_id": "Z",
            "target_service": "fake_a",
            "target_track_id": "z2",
        }
        real = w.client.post(
            _copy_url(sync_id),
            json={"dry_run": False, "preview_job_id": preview, "picks": [pick]},
        )
        drain(w.client)
        remembered = services(w.client).repos.matches.get(
            TrackKey(ServiceId("fake_b"), "Z"), ServiceId("fake_a")
        )
    assert real.status_code == 202, real.text
    assert w.a.playlists["P1"].track_ids == ("a", "b", "c", "z2")
    assert w.b.playlists["B1"].track_ids == ("A", "B", "C", "Z")
    assert remembered is not None and remembered.target_track_id == "z2"


def test_copy_to_source_while_busy_409(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        sync_id = _user_added_z(w)
        run_now(w.client, sync_id)
        busy = w.client.post(_copy_url(sync_id), json={})
        drain(w.client)
    assert busy.status_code == 409


def test_copy_to_source_copies_only_the_previewed_songs(
    app_factory: AppFactory,
) -> None:
    with world(app_factory) as w:
        sync_id = _user_added_z(w)
        preview = w.client.post(_copy_url(sync_id), json={}).json()["job_id"]
        drain(w.client)
        y = track("fake_b", "Y", title="y")
        z = track("fake_b", "Z", title="z")
        w.a.catalog = (*w.a.catalog, track("fake_a", "y", title="y"))
        w.b.add_playlist(
            snapshot("fake_b", "B1", ["A", "B", "C", "Z", "Y"], tracks={"Y": y, "Z": z})
        )
        run_now(w.client, sync_id)
        drain(w.client)
        assert [t["track_id"] for t in _added(w, sync_id)] == ["Z", "Y"]
        real = w.client.post(
            _copy_url(sync_id), json={"dry_run": False, "preview_job_id": preview}
        )
        drain(w.client)
        left = _added(w, sync_id)
    assert real.status_code == 202, real.text
    assert w.a.playlists["P1"].track_ids == ("a", "b", "c", "z")
    assert [t["track_id"] for t in left] == ["Y"]


def _added(w: Any, sync_id: str) -> list[dict[str, Any]]:
    out = w.client.get(f"/api/syncs/{sync_id}").json()
    added: list[dict[str, Any]] = out["destinations"][0]["added_by_you"]
    return added


def test_copy_preview_is_not_the_syncs_last_run(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        sync_id = _user_added_z(w)
        before = w.client.get(f"/api/syncs/{sync_id}").json()["last_run"]
        w.client.post(_copy_url(sync_id), json={})
        drain(w.client)
        after = w.client.get(f"/api/syncs/{sync_id}").json()["last_run"]
        kinds = [r["kind"] for r in w.client.get(f"/api/syncs/{sync_id}/runs").json()]
    assert before["kind"] == "sync"
    assert after == before
    assert kinds[0] == "copy_to_source"
