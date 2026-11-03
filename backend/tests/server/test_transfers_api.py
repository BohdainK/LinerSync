from dataclasses import replace
from typing import Any

from fastapi.testclient import TestClient

from linersync.domain.jobs import BulkTransferJob, JobStatus
from linersync.domain.tracks import ServiceId, TrackKey
from tests.factories import snapshot, track
from tests.server.conftest import AppFactory, drain, services
from tests.server.helpers import World, world

SRC = {"source_service": "fake_a", "playlist_id": "P1", "destination": "fake_b"}


def preview(client: TestClient, **body: Any) -> dict[str, Any]:
    response = client.post("/api/transfers", json={**SRC, "dry_run": True, **body})
    assert response.status_code == 202, response.text
    drain(client)
    done: dict[str, Any] = client.get(f"/api/jobs/{response.json()['job_id']}").json()
    return done


def from_preview(client: TestClient, job_id: str, picks: list[Any] = []) -> Any:  # noqa: B006
    return client.post(
        "/api/transfers/from-preview", json={"preview_job_id": job_id, "picks": picks}
    )


def pick_b9() -> dict[str, str]:
    return {
        "source_service": "fake_a",
        "source_track_id": "b",
        "target_service": "fake_b",
        "target_track_id": "B9",
    }


def created_ids(w: World) -> list[str]:
    [pid] = [p for p in w.b.playlists if p != "B1"]
    return [e.track.track_id for e in w.b.playlists[pid].entries]


def test_from_preview_starts_real_transfer_with_picks(
    app_factory: AppFactory,
) -> None:
    with world(app_factory) as w:
        w.b.catalog = (*w.b.catalog, track("fake_b", "B9", title="b nine"))
        prev = preview(w.client)
        response = from_preview(w.client, prev["job_id"], [pick_b9()])
        assert response.status_code == 202, response.text
        queued = response.json()
        drain(w.client)
        done = w.client.get(f"/api/jobs/{queued['job_id']}").json()
        remembered = services(w.client).repos.matches.get(
            TrackKey(ServiceId("fake_a"), "b"), ServiceId("fake_b")
        )
        ids = created_ids(w)
    assert (queued["kind"], queued["label"]) == ("transfer", "Transfer to Fake B")
    assert done["status"] == "succeeded"
    assert remembered is not None and remembered.target_track_id == "B9"
    assert ids == ["A", "B9", "C"]


def test_from_preview_bulk_keeps_kind_and_label(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        w.a.add_playlist(snapshot("fake_a", "P2", ["b"], name="Other"))
        items = [
            {
                "source_service": "fake_a",
                "playlist_id": pid,
                "destination_services": ["fake_b"],
            }
            for pid in ("P1", "P2")
        ]
        response = w.client.post(
            "/api/transfers/bulk", json={"items": items, "dry_run": True}
        )
        drain(w.client)
        prev = w.client.get(f"/api/jobs/{response.json()['job_id']}").json()
        started = from_preview(w.client, prev["job_id"]).json()
        drain(w.client)
        done = w.client.get(f"/api/jobs/{started['job_id']}").json()
    assert (started["kind"], started["label"]) == (
        "bulk_transfer",
        "Transfer 2 playlists",
    )
    assert done["status"] == "succeeded"
    assert len([p for p in w.b.playlists if p != "B1"]) == 2


def test_from_preview_rejects_foreign_run(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        real = w.client.post("/api/transfers", json=SRC).json()
        drain(w.client)
        non_dry = from_preview(w.client, real["job_id"], [pick_b9()])
        unknown = from_preview(w.client, "nope", [pick_b9()])
        matches = services(w.client).repos.matches.list_all()
    assert non_dry.status_code == 422
    assert unknown.status_code == 422
    assert matches == []


def test_from_preview_requires_every_run(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        prev = preview(w.client)
        store = services(w.client).job_store
        stored = store.get(prev["job_id"])
        assert stored is not None
        lost = replace(
            stored,
            request=BulkTransferJob((stored.request, stored.request)),  # type: ignore[arg-type]
        )
        store.save(lost)
        refused = from_preview(w.client, prev["job_id"], [pick_b9()])
        failed = replace(stored, status=JobStatus.FAILED)
        store.save(failed)
        not_done = from_preview(w.client, prev["job_id"], [pick_b9()])
        matches = services(w.client).repos.matches.list_all()
    assert len(stored.run_ids) == 1
    assert refused.status_code == not_done.status_code == 409
    assert matches == []
