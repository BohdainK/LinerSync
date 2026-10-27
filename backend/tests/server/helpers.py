"""Shared helpers for the server API tests: a two-service world and sync calls."""

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from fastapi.testclient import TestClient

from linersync.adapters.fake.connector import FakeConnector
from linersync.adapters.fake.plugin import FakePlugin
from linersync.domain.capabilities import Capability
from linersync.domain.tracks import ServiceId
from tests.factories import snapshot, track
from tests.server.conftest import AppFactory, drain
from tests.server.plugins import registry

FULL = frozenset(Capability)
NO_REMOVE = FULL - {Capability.REMOVE}


@dataclass
class World:
    client: TestClient
    a: FakeConnector
    b: FakeConnector


@contextmanager
def world(
    app_factory: AppFactory,
    *,
    b_caps: frozenset[Capability] = FULL,
    connect_b: bool = True,
    source: tuple[str, ...] = ("a", "b", "c"),
    catalog: Sequence[str] = ("a", "b", "c"),
    make_plugin: Callable[[FakeConnector], FakePlugin] = FakePlugin,
    **app_overrides: Any,
) -> Iterator[World]:
    """Fake A P1 (`source`) and an empty Fake B B1 whose catalog holds the
    upper-cased `catalog` ids, titled by their lower-case id. `app_overrides`
    go to `app_factory`."""
    a = FakeConnector(ServiceId("fake_a"), "Fake A", FULL)
    b = FakeConnector(
        ServiceId("fake_b"),
        "Fake B",
        b_caps,
        catalog=[track("fake_b", t.upper(), title=t) for t in catalog],
    )
    a.add_playlist(snapshot("fake_a", "P1", list(source)))
    a.add_playlist(snapshot("fake_a", "P2", []))
    b.add_playlist(snapshot("fake_b", "B1", []))
    app = app_factory(
        registry=registry(make_plugin(a), make_plugin(b)), **app_overrides
    )
    with TestClient(app) as client:
        assert (
            client.post("/api/auth/login", json={"password": "pw"}).status_code == 204
        )
        connect(client, "fake_a")
        if connect_b:
            connect(client, "fake_b")
        yield World(client, a, b)


def connect(client: TestClient, service: str) -> None:
    response = client.put(
        f"/api/connections/{service}/credentials", json={"values": {"account": "me"}}
    )
    assert response.status_code == 200


def new_sync(
    name: str = "Mix",
    destinations: list[dict[str, Any]] | None = None,
    schedule: str | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "source": {"service": "fake_a", "playlist_id": "P1"},
        "destinations": destinations
        if destinations is not None
        else [{"service": "fake_b", "playlist_id": "B1"}],
        "schedule": schedule,
    }


def create(client: TestClient, **kwargs: Any) -> dict[str, Any]:
    response = client.post("/api/syncs", json=new_sync(**kwargs))
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def run_now(client: TestClient, sync_id: str, dry_run: bool = False) -> Any:
    return client.post(f"/api/syncs/{sync_id}/run", json={"dry_run": dry_run})


# -- backups ------------------------------------------------------------------
ROAD_TRIP = ["a", "b"]


def back_up(client: TestClient) -> None:
    response = client.post("/api/backups/run", json={})
    assert response.status_code == 202, response.text
    drain(client)


def archive_p2(w: World) -> dict[str, Any]:
    """Seed P2 "Road trip" with songs Fake B also has, back up, delete P2 and
    back up again; returns P2's archived entry."""
    w.a.add_playlist(snapshot("fake_a", "P2", ROAD_TRIP, name="Road trip"))
    back_up(w.client)
    w.a.delete_playlist("P2")
    back_up(w.client)
    archived = w.client.get("/api/backups/playlists?archived=true").json()
    assert [e["playlist_id"] for e in archived] == ["P2"]
    entry: dict[str, Any] = archived[0]
    return entry


def latest_version(client: TestClient, entry_id: str) -> str:
    detail = client.get(f"/api/backups/playlists/{entry_id}").json()
    version_id: str = detail["history"][0]["version_id"]
    return version_id
