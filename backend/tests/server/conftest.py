from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from anyio.from_thread import BlockingPortal
from fastapi import FastAPI
from fastapi.testclient import TestClient

from linersync.adapters.memory.backups import StaticCovers
from linersync.adapters.memory.runtime import FixedClock
from linersync.server.app import create_app
from linersync.server.config import ServerConfig
from linersync.server.state import AppServices

START = datetime(2026, 1, 1, tzinfo=UTC)

AppFactory = Callable[..., FastAPI]


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(START)


@pytest.fixture
def app_factory(tmp_path: Path, clock: FixedClock) -> AppFactory:
    """Build an app. ServerConfig fields and create_app keywords are overrides.
    The background worker is off unless `start_worker=True`: tests run jobs
    with `drain(client)`. Covers come from a `StaticCovers()` unless `covers=`
    is given, so no test fetches over the network."""

    def build(**overrides: Any) -> FastAPI:
        registry = overrides.pop("registry", None)
        new_id = overrides.pop("new_id", None)
        used_clock = overrides.pop("clock", clock)
        start_worker = overrides.pop("start_worker", False)
        covers = overrides.pop("covers", None) or StaticCovers()
        fields: dict[str, Any] = {"admin_password": "pw", "data_dir": tmp_path}
        fields.update(overrides)
        return create_app(
            ServerConfig(**fields),
            registry=registry,
            clock=used_clock,
            new_id=new_id,
            start_worker=start_worker,
            covers=covers,
        )

    return build


@pytest.fixture
def client(app_factory: AppFactory) -> Iterator[TestClient]:
    with TestClient(app_factory()) as test_client:
        yield test_client


@pytest.fixture
def signed_in(client: TestClient) -> TestClient:
    assert client.post("/api/auth/login", json={"password": "pw"}).status_code == 204
    return client


def services(client: TestClient) -> AppServices:
    app: FastAPI = client.app  # type: ignore[assignment]
    found: AppServices = app.state.services
    return found


def portal(client: TestClient) -> BlockingPortal:
    """The app's event loop; only while the client is used as a context manager."""
    assert client.portal is not None
    return client.portal


def drain(client: TestClient) -> None:
    """Run every queued job to its end on the app's event loop."""

    async def run_all() -> None:
        while await services(client).jobs.run_next() is not None:
            pass

    portal(client).call(run_all)
