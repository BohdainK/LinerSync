"""End-to-end: the real lifespan and background worker run the jobs."""

import hashlib
import io
import json
import os
import re
import time
import zipfile
from collections.abc import Iterator, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.routing import BaseRoute

from linersync.adapters.fake.connector import FakeConnector
from linersync.adapters.fake.plugin import FakePlugin
from linersync.adapters.memory.backups import StaticCovers
from linersync.adapters.system import SystemClock
from linersync.domain.backups import CoverImage
from linersync.domain.jobs import Job, JobStatus, SyncJob
from linersync.domain.operations import MemberPlan, SyncPlan
from linersync.domain.playlists import PlaylistDraft
from linersync.domain.runs import RunKind, RunStatus, SyncRun, Trigger
from linersync.domain.tracks import ServiceId
from linersync.server.cli import main
from linersync.server.state import AppServices
from tests.factories import snapshot, track
from tests.server.conftest import AppFactory, drain
from tests.server.helpers import (
    FULL,
    back_up,
    connect,
    create,
    latest_version,
    new_sync,
    run_now,
    world,
)
from tests.server.plugins import registry

TIMEOUT_SECONDS = 5.0
_PATH_PARAM = re.compile(r"\{[^}]+\}")
_PUBLIC = {"/api/health", "/api/auth/login"}


def wait_finished(client: TestClient, job_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while True:
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        if body["status"] not in ("queued", "running"):
            return body
        assert time.monotonic() < deadline, f"job still {body['status']}"
        time.sleep(0.01)


def get_run(client: TestClient, run_id: str) -> dict[str, Any]:
    response = client.get(f"/api/runs/{run_id}")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def b1_track_ids(connector: FakeConnector) -> list[str]:
    return [e.track.track_id for e in connector.playlists["B1"].entries]


def fakes() -> tuple[FakeConnector, FakeConnector]:
    a = FakeConnector(ServiceId("fake_a"), "Fake A", FULL)
    b = FakeConnector(
        ServiceId("fake_b"),
        "Fake B",
        FULL,
        catalog=[track("fake_b", t.upper(), title=t) for t in ("a", "b", "c")],
    )
    a.add_playlist(snapshot("fake_a", "P1", ["a", "b", "c"]))
    b.add_playlist(snapshot("fake_b", "B1", []))
    return a, b


def test_full_flow(app_factory: AppFactory) -> None:
    a, b = fakes()
    app = app_factory(
        registry=registry(FakePlugin(a), FakePlugin(b)),
        clock=SystemClock(),
        start_worker=True,
    )
    with TestClient(app) as client:
        assert (
            client.post("/api/auth/login", json={"password": "pw"}).status_code == 204
        )
        for service in ("fake_a", "fake_b"):
            connect(client, service)
            tested = client.post(f"/api/connections/{service}/test", json={})
            assert tested.json()["ok"] is True
            info = client.get(f"/api/connections/{service}").json()
            assert info["status"] == "connected"
        sync_id = create(client)["sync_id"]

        preview_job = client.post("/api/syncs/preview", json=new_sync()).json()
        preview_done = wait_finished(client, preview_job["job_id"])
        preview = get_run(client, preview_done["run_ids"][0])
        assert b.playlists["B1"].entries == ()

        sync_job = run_now(client, sync_id).json()
        sync_done = wait_finished(client, sync_job["job_id"])
        [run_id] = sync_done["run_ids"]
        run = get_run(client, run_id)
        synced = b1_track_ids(b)
        recent = client.get("/api/home").json()["recent"]

        undo_job = client.post(f"/api/runs/{run_id}/undo", json={}).json()
        undo_done = wait_finished(client, undo_job["job_id"])
        restored = b.playlists["B1"].entries
    assert preview_done["status"] == "succeeded"
    assert (preview["dry_run"], preview["unresolved"]) == (True, [])
    assert sync_done["status"] == "succeeded"
    assert run["status"] == "succeeded"
    [dst] = run["members"]
    [add] = dst["operations"]
    assert [t["title"] for t in add["tracks"]] == ["a", "b", "c"]
    assert synced == ["A", "B", "C"]
    assert sync_job["job_id"] in [j["job_id"] for j in recent]
    assert undo_done["status"] == "succeeded"
    assert restored == ()


def test_state_survives_restart(app_factory: AppFactory) -> None:
    a, b = fakes()
    plugins = registry(FakePlugin(a), FakePlugin(b))
    clock = SystemClock()
    with TestClient(app_factory(registry=plugins, clock=clock)) as client:
        assert (
            client.post("/api/auth/login", json={"password": "pw"}).status_code == 204
        )
        connect(client, "fake_a")
        connect(client, "fake_b")
        sync_id = create(client)["sync_id"]
        first = run_now(client, sync_id).json()
        # No worker in this app: run the first job by hand, leave the second queued.
        drain(client)
        queued = run_now(client, sync_id).json()
        assert queued["status"] == "queued"

    app = app_factory(registry=plugins, clock=clock, start_worker=True)
    with TestClient(app) as client:
        assert (
            client.post("/api/auth/login", json={"password": "pw"}).status_code == 204
        )
        later = wait_finished(client, queued["job_id"])
        sync = client.get(f"/api/syncs/{sync_id}").json()
        runs = client.get(f"/api/syncs/{sync_id}/runs").json()
        info = client.get("/api/connections/fake_b").json()
        earlier = client.get(f"/api/jobs/{first['job_id']}").json()
    assert sync["name"] == "Mix"
    assert earlier["status"] == "succeeded"
    assert later["status"] == "succeeded"
    assert {r["run_id"] for r in runs} == {*earlier["run_ids"], *later["run_ids"]}
    assert info["status"] != "not_set_up"
    assert b1_track_ids(b) == ["A", "B", "C"]


def test_paused_sync_drops_its_scheduled_run(app_factory: AppFactory) -> None:
    with world(app_factory, clock=SystemClock(), start_worker=True) as w:
        sync_id = create(w.client)["sync_id"]
        before = w.client.get(f"/api/syncs/{sync_id}").json()
        w.a.drop_playlist("P1")
        wait_finished(w.client, run_now(w.client, sync_id).json()["job_id"])
        after = w.client.get(f"/api/syncs/{sync_id}").json()
        next_up = w.client.get("/api/home").json()["next_up"]
    assert before["next_run_at"] is not None
    assert after["status"] == "paused"
    assert after["next_run_at"] is None
    assert sync_id not in [n["sync_id"] for n in next_up]


def _flatten(routes: Sequence[BaseRoute]) -> Iterator[APIRoute]:
    """FastAPI keeps included routers as wrappers; walk into them."""
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        included = getattr(route, "original_router", None)
        if included is not None:
            yield from _flatten(included.routes)


def _api_routes(app: FastAPI) -> list[APIRoute]:
    return [r for r in _flatten(app.routes) if r.path.startswith("/api/")]


def test_every_api_route_requires_session(app_factory: AppFactory) -> None:
    app = app_factory()
    checked = 0
    with TestClient(app) as client:
        for route in _api_routes(app):
            if route.path in _PUBLIC:
                continue
            url = _PATH_PARAM.sub("x", route.path)
            for method in sorted(route.methods or ()):
                if method in ("GET", "HEAD"):
                    response = client.request(method, url)
                else:
                    response = client.request(method, url, json={})
                checked += 1
                assert response.status_code == 401, (method, url, response.text)
    assert checked > 30


def test_openapi_is_complete(app_factory: AppFactory) -> None:
    app = app_factory()
    with TestClient(app) as client:
        response = client.get("/api/openapi.json")
    assert response.status_code == 200
    spec = response.json()
    ids = [
        operation["operationId"]
        for path in spec["paths"].values()
        for operation in path.values()
    ]
    assert len(ids) == len(set(ids))
    for route in _api_routes(app):
        if not route.endpoint.__module__.startswith("linersync.server.api"):
            continue
        documented = route.response_model is not None or route.status_code in (
            204,
            303,
        )
        streams = any(
            "content" in r and "application/json" not in r["content"]
            for r in route.responses.values()
        )
        assert documented or streams, route.path


def test_cli_without_password_exits_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in list(os.environ):
        if name.startswith("LINERSYNC_"):
            monkeypatch.delenv(name)
    assert main(["serve"]) == 2
    assert "LINERSYNC_ADMIN_PASSWORD" in capsys.readouterr().err


def test_cli_serve_bounds_graceful_shutdown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name in list(os.environ):
        if name.startswith("LINERSYNC_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("LINERSYNC_ADMIN_PASSWORD", "pw")
    monkeypatch.setenv("LINERSYNC_DATA_DIR", str(tmp_path))
    calls: list[dict[str, Any]] = []

    def fake_run(app: object, **kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(uvicorn, "run", fake_run)
    assert main(["serve"]) == 0
    [kwargs] = calls
    assert kwargs["timeout_graceful_shutdown"] == 5
    assert (kwargs["host"], kwargs["port"]) == ("127.0.0.1", 8000)


def test_startup_recovers_work_cut_off_by_a_stop(app_factory: AppFactory) -> None:
    a, b = fakes()
    plugins = registry(FakePlugin(a), FakePlugin(b))
    clock = SystemClock()
    first = app_factory(registry=plugins, clock=clock)
    with TestClient(first) as client:
        assert (
            client.post("/api/auth/login", json={"password": "pw"}).status_code == 204
        )
        connect(client, "fake_a")
        connect(client, "fake_b")
        sync_id = create(client)["sync_id"]
        # What a stop in the middle of a sync's writes leaves behind (no worker
        # runs in this app).
        stored: AppServices = first.state.services
        now = clock.now()
        plan = SyncPlan(
            sync_id,
            (MemberPlan("dst0", (), (), ("A",), (), PlaylistDraft("Mix")),),
            (),
        )
        stored.repos.runs.save(
            SyncRun(
                run_id="cut",
                sync_id=sync_id,
                kind=RunKind.SYNC,
                trigger=Trigger.MANUAL,
                dry_run=False,
                status=RunStatus.RUNNING,
                started_at=now,
                plan=plan,
            )
        )
        stored.repos.snapshots.save_pre_write(
            "cut", "dst0", snapshot("fake_b", "B1", [])
        )
        stored.job_store.save(
            Job(
                "running",
                SyncJob(sync_id, Trigger.MANUAL),
                JobStatus.RUNNING,
                now,
                "Sync “Mix”",
                started_at=now,
                run_ids=("cut",),
            )
        )
        stored.job_store.save(
            Job(
                "queued",
                SyncJob(sync_id, Trigger.MANUAL, dry_run=True),
                JobStatus.QUEUED,
                now,
                "Dry run “Mix”",
            )
        )

    second = app_factory(registry=plugins, clock=clock, start_worker=True)
    with TestClient(second) as client:
        assert (
            client.post("/api/auth/login", json={"password": "pw"}).status_code == 204
        )
        interrupted = client.get("/api/jobs/running").json()
        run = get_run(client, "cut")
        queued = wait_finished(client, "queued")
    assert interrupted["status"] == "interrupted"
    assert interrupted["error"] == "LinerSync stopped while this was running"
    assert run["status"] == "aborted"
    assert run["error"] == "LinerSync stopped during this run"
    assert run["undoable"] is True
    assert [(m["member_id"], m["status"]) for m in run["members"]] == [
        ("dst0", "failed")
    ]
    assert queued["status"] == "succeeded"


def test_backup_archive_restore_and_export_survive_restart(
    app_factory: AppFactory,
) -> None:
    url = "https://img.example/p2.png"
    data = b"\x89PNG-e2e"
    image = CoverImage(hashlib.sha256(data).hexdigest(), "image/png", data)
    covers = StaticCovers({url: image})
    with world(app_factory, covers=covers) as first:
        p2 = snapshot("fake_a", "P2", ["a", "b"])
        first.a.add_playlist(replace(p2, meta=replace(p2.meta, cover_url=url)))
        sync_id = create(first.client)["sync_id"]
        drain(first.client)
        assert run_now(first.client, sync_id).status_code == 202
        drain(first.client)
        back_up(first.client)
        first.a.drop_playlist("P2")
        back_up(first.client)
        a, b = first.a, first.b

    with TestClient(
        app_factory(registry=registry(FakePlugin(a), FakePlugin(b)), covers=covers)
    ) as client:
        assert (
            client.post("/api/auth/login", json={"password": "pw"}).status_code == 204
        )
        archived = client.get("/api/backups/playlists?archived=true").json()
        live = client.get("/api/backups/playlists?archived=false").json()
        [entry] = archived
        version_id = latest_version(client, entry["entry_id"])
        runs = client.get("/api/backups/runs").json()
        cover = client.get(entry["cover_url"])

        before = set(a.playlists)
        restore = client.post(
            f"/api/backups/versions/{version_id}/restore",
            content="null",
            headers={"Content-Type": "application/json"},
        )
        assert restore.status_code == 202, restore.text
        drain(client)

        exported = client.get("/api/export")
        imported = client.post(
            "/api/import",
            content=exported.text,
            headers={"Content-Type": "application/json"},
        )
        csv = client.get("/api/export/csv")

    assert (entry["playlist_id"], entry["service"]) == ("P2", "fake_a")
    assert entry["archived_at"] is not None
    assert sorted(e["playlist_id"] for e in live) == ["B1", "P1"]
    assert len(runs) == 4
    assert (cover.status_code, cover.content) == (200, data)
    assert len(set(a.playlists) - before) == 1
    assert exported.status_code == 200
    assert imported.status_code == 200, imported.text
    assert all(n == 0 for n in imported.json()["added"].values())
    assert csv.status_code == 200
    with zipfile.ZipFile(io.BytesIO(csv.content)) as z:
        assert "archive/Mix (fake_a).csv" in z.namelist()
    assert json.loads(exported.text)["format"] == "linersync-export"
