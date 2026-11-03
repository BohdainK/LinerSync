import hashlib
from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from linersync.adapters.fake.connector import FakeConnector
from linersync.adapters.fake.plugin import FakePlugin
from linersync.adapters.memory.backups import StaticCovers
from linersync.domain.backups import (
    BackedUpPlaylist,
    BackupRun,
    BackupStatus,
    CoverImage,
)
from linersync.domain.tracks import ServiceId
from tests.factories import snapshot
from tests.server.conftest import START, AppFactory, drain, services
from tests.server.helpers import (
    FULL,
    ROAD_TRIP,
    archive_p2,
    back_up,
    latest_version,
    world,
)
from tests.server.plugins import registry


def _job(client: TestClient, job_id: str) -> dict[str, Any]:
    response = client.get(f"/api/jobs/{job_id}")
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    return body


def _delete(client: TestClient, url: str) -> Any:
    return client.request("DELETE", url, json={})


def _by_service(summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {s["service"]: s for s in summary["services"]}


def test_backup_now_backs_up_connected_services(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        response = w.client.post("/api/backups/run", json={})
        assert response.status_code == 202, response.text
        job = response.json()
        assert job["kind"] == "backup"
        assert job["label"] == "Back up all services"
        assert job["backup_ids"] == []
        summary = w.client.get("/api/backups").json()
        assert summary["active_job"]["job_id"] == job["job_id"]
        drain(w.client)
        done = _job(w.client, job["job_id"])
        assert done["status"] == "succeeded"
        assert len(done["backup_ids"]) == 2
        summary = w.client.get("/api/backups").json()
        assert summary["active_job"] is None
        assert summary["schedule"] == "0 3 * * *"
        assert summary["next_backup_at"] is not None
        assert [s["display_name"] for s in summary["services"]] == ["Fake A", "Fake B"]
        a, b = summary["services"]
        assert (a["playlists"], b["playlists"]) == (2, 1)
        assert a["connected"] is True
        assert (a["archived"], a["missing"]) == (0, 0)
        assert a["last_run"]["status"] == "succeeded"
        assert b["last_run"]["status"] == "succeeded"
        assert a["last_run"]["saved"] == 2


def test_backup_one_service_label(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        response = w.client.post("/api/backups/run", json={"services": ["fake_b"]})
        assert response.status_code == 202
        assert response.json()["label"] == "Back up Fake B"
        drain(w.client)
        summary = _by_service(w.client.get("/api/backups").json())
        assert summary["fake_b"]["playlists"] == 1
        assert summary["fake_a"]["last_run"] is None


def test_summary_lists_services_with_stored_data(app_factory: AppFactory) -> None:
    with world(app_factory, connect_b=False) as w:
        store = services(w.client).backups
        store.save_entry(
            BackedUpPlaylist(
                "e1",
                ServiceId("gone"),
                "G1",
                "Old",
                "v1",
                3,
                1,
                START,
                START,
                archived_at=START,
            )
        )
        store.save_run(
            BackupRun("r1", ServiceId("gone"), START, START, BackupStatus.SUCCEEDED)
        )
        summary = w.client.get("/api/backups").json()
        found = _by_service(summary)
        assert set(found) == {"fake_a", "gone"}
        assert found["gone"]["display_name"] == "gone"
        assert found["gone"]["connected"] is False
        assert (found["gone"]["playlists"], found["gone"]["archived"]) == (0, 1)
        assert found["gone"]["last_run"]["backup_id"] == "r1"


def test_backup_run_validates_services(app_factory: AppFactory, tmp_path: Path) -> None:
    with world(app_factory, connect_b=False) as w:
        for name in ("nope", "backup"):
            response = w.client.post("/api/backups/run", json={"services": [name]})
            assert response.status_code == 422
            assert response.json()["detail"] == f"Unknown service {name}"
        response = w.client.post("/api/backups/run", json={"services": ["fake_b"]})
        assert response.status_code == 422
        assert response.json()["detail"] == "Connect Fake B first"

    a = FakeConnector(ServiceId("fake_a"), "Fake A", FULL)
    app = app_factory(registry=registry(FakePlugin(a)), data_dir=tmp_path / "fresh")
    with TestClient(app) as client:
        assert (
            client.post("/api/auth/login", json={"password": "pw"}).status_code == 204
        )
        for body in ("null", "{}", '{"services": []}'):
            response = client.post(
                "/api/backups/run",
                content=body,
                headers={"Content-Type": "application/json"},
            )
            assert response.status_code == 422
            assert response.json()["detail"] == "Connect a service first"


def test_backup_runs_listed_and_fetched(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        job = w.client.post("/api/backups/run", json={}).json()
        drain(w.client)
        backup_ids = _job(w.client, job["job_id"])["backup_ids"]
        runs = w.client.get("/api/backups/runs").json()
        assert {r["backup_id"] for r in runs} == set(backup_ids)
        only_a = w.client.get("/api/backups/runs?service=fake_a").json()
        assert [r["service"] for r in only_a] == ["fake_a"]
        assert only_a[0]["playlists"] == 2
        assert only_a[0]["errors"] == []
        assert len(w.client.get("/api/backups/runs?limit=1").json()) == 1
        assert w.client.get("/api/backups/runs?limit=0").status_code == 422
        assert w.client.get("/api/backups/runs?limit=201").status_code == 422
        one = w.client.get(f"/api/backups/runs/{only_a[0]['backup_id']}")
        assert one.status_code == 200
        assert one.json() == only_a[0]
        missing = w.client.get("/api/backups/runs/nope")
        assert missing.status_code == 404
        assert missing.json()["detail"] == "Unknown backup run"


def test_cover_served_with_cache_headers(app_factory: AppFactory) -> None:
    url = "https://img.example/p3.png"
    data = b"\x89PNG-bytes"
    image = CoverImage(hashlib.sha256(data).hexdigest(), "image/png", data)
    with world(app_factory, covers=StaticCovers({url: image})) as w:
        snap = snapshot("fake_a", "P3", ["a"], name="Covered")
        w.a.add_playlist(replace(snap, meta=replace(snap.meta, cover_url=url)))
        w.client.post("/api/backups/run", json={"services": ["fake_a"]})
        drain(w.client)
        response = w.client.get(f"/api/backups/covers/{image.sha256}")
        assert response.status_code == 200
        assert response.content == data
        assert response.headers["content-type"] == "image/png"
        assert (
            response.headers["cache-control"] == "private, max-age=31536000, immutable"
        )
        assert response.headers["x-content-type-options"] == "nosniff"
        assert (
            response.headers["content-security-policy"] == "default-src 'none'; sandbox"
        )
        missing = w.client.get(f"/api/backups/covers/{'0' * 64}")
        assert missing.status_code == 404
        assert missing.json()["detail"] == "Not found"


def test_backup_routes_need_session(app_factory: AppFactory) -> None:
    with TestClient(app_factory()) as client:
        for method, url in (
            ("GET", "/api/backups"),
            ("POST", "/api/backups/run"),
            ("GET", "/api/backups/runs"),
            ("GET", "/api/backups/runs/x"),
            ("GET", "/api/backups/covers/x"),
            ("GET", "/api/backups/playlists"),
            ("GET", "/api/backups/playlists/x"),
            ("DELETE", "/api/backups/playlists/x"),
            ("GET", "/api/backups/versions/x"),
            ("POST", "/api/backups/versions/x/restore"),
        ):
            response = client.request(
                method, url, json={} if method in ("POST", "DELETE") else None
            )
            assert response.status_code == 401, url


# -- archive, restore and backup as a source ---------------------------------
def test_archive_listed_and_detail_has_history(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        entry = archive_p2(w)
        assert entry["service"] == "fake_a"
        assert entry["name"] == "Road trip"
        assert entry["track_count"] == 2
        assert entry["versions"] == 1
        assert entry["archived_at"] is not None
        assert entry["cover_url"] is None
        live = w.client.get("/api/backups/playlists?archived=false").json()
        assert {e["playlist_id"] for e in live} == {"P1", "B1"}
        only_b = w.client.get("/api/backups/playlists?service=fake_b").json()
        assert [e["playlist_id"] for e in only_b] == ["B1"]
        assert len(w.client.get("/api/backups/playlists").json()) == 3

        response = w.client.get(f"/api/backups/playlists/{entry['entry_id']}")
        assert response.status_code == 200
        detail = response.json()
        assert detail["entry_id"] == entry["entry_id"]
        assert len(detail["history"]) == 1
        version = detail["history"][0]
        assert version["entry_id"] == entry["entry_id"]
        assert (version["name"], version["track_count"]) == ("Road trip", 2)

        response = w.client.get(f"/api/backups/versions/{version['version_id']}")
        assert response.status_code == 200
        tracks = response.json()["tracks"]
        assert [t["position"] for t in tracks] == [1, 2]
        assert [t["track"]["track_id"] for t in tracks] == ROAD_TRIP
        assert all(t["track"]["known"] for t in tracks)
        assert tracks[0]["track"]["service"] == "fake_a"

        for url in ("/api/backups/playlists/nope", "/api/backups/versions/nope"):
            missing = w.client.get(url)
            assert missing.status_code == 404
            assert missing.json()["detail"] == "Unknown backup"


def test_cover_url_points_at_stored_cover(app_factory: AppFactory) -> None:
    url = "https://img.example/p3.png"
    data = b"\x89PNG-bytes"
    image = CoverImage(hashlib.sha256(data).hexdigest(), "image/png", data)
    with world(app_factory, covers=StaticCovers({url: image})) as w:
        snap = snapshot("fake_a", "P3", ["a"], name="Covered")
        w.a.add_playlist(replace(snap, meta=replace(snap.meta, cover_url=url)))
        back_up(w.client)
        entries = w.client.get("/api/backups/playlists?service=fake_a").json()
        covered = next(e for e in entries if e["playlist_id"] == "P3")
        assert covered["cover_url"] == f"/api/backups/covers/{image.sha256}"
        detail = w.client.get(f"/api/backups/playlists/{covered['entry_id']}").json()
        assert detail["history"][0]["cover_url"] == covered["cover_url"]


def test_delete_archived_entry(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        entry = archive_p2(w)
        url = f"/api/backups/playlists/{entry['entry_id']}"
        assert _delete(w.client, url).status_code == 204
        assert w.client.get(url).status_code == 404
        missing = _delete(w.client, url)
        assert missing.status_code == 404
        assert missing.json()["detail"] == "Unknown backup"
        live = w.client.get("/api/backups/playlists?archived=false").json()[0]
        response = _delete(w.client, f"/api/backups/playlists/{live['entry_id']}")
        assert response.status_code == 409
        assert response.json()["detail"] == "Only archived playlists can be deleted"


def test_delete_prunes_covers(app_factory: AppFactory) -> None:
    url = "https://img.example/p3.png"
    data = b"\x89PNG-bytes"
    image = CoverImage(hashlib.sha256(data).hexdigest(), "image/png", data)
    with world(app_factory, covers=StaticCovers({url: image})) as w:
        snap = snapshot("fake_a", "P3", ["a"], name="Covered")
        w.a.add_playlist(replace(snap, meta=replace(snap.meta, cover_url=url)))
        back_up(w.client)
        w.a.drop_playlist("P3")
        back_up(w.client)
        archived = w.client.get("/api/backups/playlists?archived=true").json()
        p3 = next(e for e in archived if e["playlist_id"] == "P3")
        assert w.client.get(p3["cover_url"]).status_code == 200
        response = _delete(w.client, f"/api/backups/playlists/{p3['entry_id']}")
        assert response.status_code == 204
        assert services(w.client).backups.get_cover(image.sha256) is None


def test_backup_data_locked_while_backup_queued(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        entry = archive_p2(w)
        assert w.client.post("/api/backups/run", json={}).status_code == 202
        response = _delete(w.client, f"/api/backups/playlists/{entry['entry_id']}")
        assert response.status_code == 409
        assert response.json()["detail"] == "Wait for the current backup to finish"
        response = _delete(w.client, "/api/connections/fake_a?delete_backups=true")
        assert response.status_code == 409
        assert response.json()["detail"] == "Wait for the current backup to finish"
        status = w.client.get("/api/connections/fake_a").json()["status"]
        assert status != "not_set_up"
        assert w.client.get(f"/api/backups/playlists/{entry['entry_id']}").is_success


def test_restore_creates_new_playlist(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        entry = archive_p2(w)
        version_id = latest_version(w.client, entry["entry_id"])
        before = set(w.a.playlists)
        response = w.client.post(
            f"/api/backups/versions/{version_id}/restore",
            content="null",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 202, response.text
        job = response.json()
        assert job["label"] == "Restore “Road trip” to Fake A"
        drain(w.client)
        done = _job(w.client, job["job_id"])
        assert done["status"] == "succeeded"
        (new_id,) = set(w.a.playlists) - before
        restored = w.a.playlists[new_id]
        assert restored.meta.name == "Road trip"
        assert list(restored.track_ids) == ROAD_TRIP
        run_id = done["run_ids"][0]
        run = w.client.get(f"/api/runs/{run_id}").json()
        assert run["status"] == "succeeded"
        assert run["members"][0]["created_playlist_id"] == new_id
        # RunOut lists destinations only; the source lives in the run context.
        ctx = services(w.client).contexts.get(run_id)
        assert ctx is not None
        src = next(m for m in ctx.members if m.member_id == "src")
        assert (src.service, src.playlist_id) == ("backup", version_id)


def test_restore_to_other_service_and_options(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        entry = archive_p2(w)
        version_id = latest_version(w.client, entry["entry_id"])
        url = f"/api/backups/versions/{version_id}/restore"
        response = w.client.post(url, json={"destination": "backup"})
        assert response.status_code == 422
        response = w.client.post(url, json={"destination": "nope"})
        assert response.status_code == 422
        assert response.json()["detail"] == "Unknown service nope"
        missing = w.client.post("/api/backups/versions/nope/restore", json={})
        assert missing.status_code == 404
        assert missing.json()["detail"] == "Unknown backup"
        before = set(w.b.playlists)
        response = w.client.post(url, json={"destination": "fake_b", "dry_run": True})
        assert response.status_code == 202
        assert response.json()["label"] == "Restore “Road trip” to Fake B"
        drain(w.client)
        assert set(w.b.playlists) == before
        response = w.client.post(url, json={"destination": "fake_b"})
        drain(w.client)
        (new_id,) = set(w.b.playlists) - before
        assert list(w.b.playlists[new_id].track_ids) == ["A", "B"]


def test_restore_needs_connected_destination(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        entry = archive_p2(w)
        version_id = latest_version(w.client, entry["entry_id"])
        assert _delete(w.client, "/api/connections/fake_a").status_code == 204
        response = w.client.post(f"/api/backups/versions/{version_id}/restore", json={})
        assert response.status_code == 422
        assert response.json()["detail"] == "Connect Fake A first"
        archived = w.client.get("/api/backups/playlists?archived=true").json()
        assert [e["entry_id"] for e in archived] == [entry["entry_id"]]


def test_disconnect_keeps_backups_by_default(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        back_up(w.client)
        assert _delete(w.client, "/api/connections/fake_a").status_code == 204
        assert w.client.get("/api/backups/playlists?service=fake_a").json() != []
        assert w.client.get("/api/backups/runs?service=fake_a").json() != []


def test_disconnect_can_delete_backups(app_factory: AppFactory) -> None:
    url = "https://img.example/p3.png"
    data = b"\x89PNG-bytes"
    image = CoverImage(hashlib.sha256(data).hexdigest(), "image/png", data)
    with world(app_factory, covers=StaticCovers({url: image})) as w:
        snap = snapshot("fake_a", "P3", ["a"], name="Covered")
        w.a.add_playlist(replace(snap, meta=replace(snap.meta, cover_url=url)))
        back_up(w.client)
        response = _delete(w.client, "/api/connections/fake_a?delete_backups=true")
        assert response.status_code == 204
        assert w.client.get("/api/connections/fake_a").json()["status"] == (
            "not_set_up"
        )
        assert w.client.get("/api/backups/playlists?service=fake_a").json() == []
        assert w.client.get("/api/backups/runs?service=fake_a").json() == []
        assert w.client.get("/api/backups/playlists?service=fake_b").json() != []
        assert services(w.client).backups.get_cover(image.sha256) is None


def test_archived_playlist_as_transfer_source(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        entry = archive_p2(w)
        before = set(w.b.playlists)
        response = w.client.post(
            "/api/transfers",
            json={
                "source_service": "backup",
                "playlist_id": entry["entry_id"],
                "destination": "fake_b",
            },
        )
        assert response.status_code == 202, response.text
        drain(w.client)
        (new_id,) = set(w.b.playlists) - before
        assert list(w.b.playlists[new_id].track_ids) == ["A", "B"]
        response = w.client.post(
            "/api/transfers",
            json={
                "source_service": "backup",
                "playlist_id": "nope",
                "destination": "fake_b",
            },
        )
        assert response.status_code == 422
        assert response.json()["detail"] == "Unknown backup"
        item = {
            "source_service": "backup",
            "playlist_id": entry["entry_id"],
            "destination_services": ["fake_b"],
        }
        response = w.client.post("/api/transfers/estimate", json={"items": [item]})
        assert response.status_code == 200, response.text
        assert response.json()["songs"] == 2
        response = w.client.post("/api/transfers/bulk", json={"items": [item]})
        assert response.status_code == 202, response.text
        bad = {**item, "playlist_id": "nope"}
        response = w.client.post("/api/transfers/bulk", json={"items": [bad]})
        assert response.status_code == 422
        assert response.json()["detail"] == "Unknown backup"
