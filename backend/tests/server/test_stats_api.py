from datetime import timedelta
from typing import Any

from fastapi.testclient import TestClient

from linersync.adapters.memory.runtime import FixedClock
from tests.server.conftest import START, AppFactory, drain
from tests.server.helpers import back_up, create, run_now, world

NO_ACTIVITY = {
    "sync_runs": 0,
    "transfer_runs": 0,
    "undo_runs": 0,
    "succeeded": 0,
    "partial": 0,
    "failed": 0,
    "waiting": 0,
    "skipped": 0,
    "songs_added": 0,
    "songs_removed": 0,
    "backups": 0,
}


def stats(client: TestClient, **params: Any) -> dict[str, Any]:
    response = client.get("/api/stats", params=params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def test_stats_needs_sign_in(client: TestClient) -> None:
    assert client.get("/api/stats").status_code == 401


def test_stats_when_nothing_happened(signed_in: TestClient) -> None:
    body = stats(signed_in)
    assert body["daily"][0] == {
        "day": "2025-12-03",
        "succeeded": 0,
        "partial": 0,
        "failed": 0,
    }
    assert body["daily"][-1]["day"] == "2026-01-01"
    assert body == {
        "days": 30,
        "since": "2025-12-03T00:00:00Z",
        "library": [],
        "syncs": 0,
        "paused_syncs": 0,
        "activity": NO_ACTIVITY,
        "daily": body["daily"],
        "last_backup_at": None,
        "matching": {"remembered": 0, "not_on_service": 0, "open_attention": 0},
    }


def test_days_must_be_in_range(signed_in: TestClient) -> None:
    for days in (0, 366, "x"):
        assert signed_in.get("/api/stats", params={"days": days}).status_code == 422
    year = stats(signed_in, days=365)
    assert (year["days"], len(year["daily"])) == (365, 365)


def test_stats_count_library_runs_and_matching(app_factory: AppFactory) -> None:
    with world(app_factory, source=("a", "b", "c", "dup")) as w:
        sync_id = create(w.client)["sync_id"]
        create(w.client, name="Other")
        w.client.patch(f"/api/syncs/{sync_id}", json={"paused": True})
        w.client.patch(f"/api/syncs/{sync_id}", json={"paused": False})
        run_now(w.client, sync_id)
        drain(w.client)
        run_now(w.client, sync_id, dry_run=True)
        drain(w.client)
        back_up(w.client)
        body = stats(w.client)
    library = {row["service"]: row for row in body["library"]}
    assert [row["display_name"] for row in body["library"]] == ["Fake A", "Fake B"]
    assert library["fake_a"] == {
        "service": "fake_a",
        "display_name": "Fake A",
        "playlists": 2,
        "songs": 4,
        "archived": 0,
        "versions": 2,
    }
    assert (library["fake_b"]["playlists"], library["fake_b"]["songs"]) == (1, 3)
    assert body["syncs"] == 2
    assert body["paused_syncs"] == 0
    assert body["activity"] == {
        **NO_ACTIVITY,
        "sync_runs": 1,
        "succeeded": 1,
        "songs_added": 3,
        "backups": 2,
    }
    assert body["last_backup_at"] is not None
    assert len(body["daily"]) == 30
    assert body["daily"][-1] == {
        "day": START.date().isoformat(),
        "succeeded": 1,
        "partial": 0,
        "failed": 0,
    }
    assert sum(d["succeeded"] for d in body["daily"]) == 1
    assert body["matching"]["open_attention"] == 1  # "dup" has no match on Fake B


def test_stats_window_leaves_out_older_runs(
    app_factory: AppFactory, clock: FixedClock
) -> None:
    with world(app_factory) as w:
        sync_id = create(w.client)["sync_id"]
        run_now(w.client, sync_id)
        drain(w.client)
        w.client.patch(f"/api/syncs/{sync_id}", json={"paused": True})
        back_up(w.client)
        clock.advance(timedelta(days=8))
        week = stats(w.client, days=7)
        month = stats(w.client)
    assert week["activity"] == NO_ACTIVITY
    assert week["paused_syncs"] == 1
    assert week["last_backup_at"] is not None
    assert (month["activity"]["sync_runs"], month["activity"]["backups"]) == (1, 2)
