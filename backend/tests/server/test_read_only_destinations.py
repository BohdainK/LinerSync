"""A service without ADD is a source only: every destination route refuses it."""

import httpx
from fastapi.testclient import TestClient

from linersync.domain.capabilities import Capability
from tests.server.conftest import AppFactory
from tests.server.helpers import (
    archive_p2,
    create,
    latest_version,
    new_sync,
    world,
)

READ_ONLY = frozenset({Capability.READ_ORDER})
DETAIL = {"detail": "Fake B is read-only, so it can't be a destination"}


def _refused(response: httpx.Response) -> None:
    assert response.status_code == 422
    assert response.json() == DETAIL


def test_create_rejects_read_only_destination(app_factory: AppFactory) -> None:
    with world(app_factory, b_caps=READ_ONLY) as w:
        _refused(w.client.post("/api/syncs", json=new_sync()))
        assert w.client.get("/api/syncs").json() == []


def test_add_destination_rejects_read_only(app_factory: AppFactory) -> None:
    with world(app_factory, b_caps=READ_ONLY) as w:
        p2 = [{"service": "fake_a", "playlist_id": "P2"}]
        sync_id = create(w.client, destinations=p2)["sync_id"]
        response = w.client.patch(
            f"/api/syncs/{sync_id}", json={"add_destinations": [{"service": "fake_b"}]}
        )
        _refused(response)


def test_transfers_reject_read_only_destination(app_factory: AppFactory) -> None:
    with world(app_factory, b_caps=READ_ONLY) as w:
        client: TestClient = w.client
        single = {
            "source_service": "fake_a",
            "playlist_id": "P1",
            "destination": "fake_b",
        }
        _refused(client.post("/api/transfers", json=single))
        item = {
            "source_service": "fake_a",
            "playlist_id": "P1",
            "destination_services": ["fake_b"],
        }
        _refused(client.post("/api/transfers/bulk", json={"items": [item]}))


def test_restore_rejects_read_only_destination(app_factory: AppFactory) -> None:
    with world(app_factory, b_caps=READ_ONLY) as w:
        entry = archive_p2(w)
        version_id = latest_version(w.client, entry["entry_id"])
        response = w.client.post(
            f"/api/backups/versions/{version_id}/restore",
            json={"destination": "fake_b"},
        )
        _refused(response)


def test_read_only_service_is_still_a_source(app_factory: AppFactory) -> None:
    with world(app_factory, b_caps=READ_ONLY) as w:
        body = new_sync(destinations=[{"service": "fake_a", "playlist_id": "P2"}])
        body["source"] = {"service": "fake_b", "playlist_id": "B1"}
        response = w.client.post("/api/syncs", json=body)
        assert response.status_code == 201, response.text
