"""The fake world the frontend's Playwright tests run against.

`python -m tests.e2e_server` serves the real app with three fake connectors and
an e2e-only route that deletes a playlist from a fake. Never installed.
"""

import argparse
import signal
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Response
from pydantic import BaseModel

from linersync.adapters.connectors.kit.discovery import PluginRegistry
from linersync.adapters.fake.connector import FakeConnector
from linersync.adapters.fake.plugin import FakePlugin
from linersync.adapters.memory.backups import StaticCovers
from linersync.domain.capabilities import Capability
from linersync.domain.tracks import ServiceId
from linersync.server.app import create_app
from linersync.server.cli import GRACEFUL_SHUTDOWN_SECONDS
from linersync.server.config import ServerConfig
from tests.factories import snapshot, track

E2E_PASSWORD = "e2e-password"

ALL = frozenset(Capability)


class StreamingFakePlugin(FakePlugin):
    category = "streaming"


class DeletePlaylist(BaseModel):
    service: str
    playlist_id: str


def build_app(data_dir: Path, public_url: str) -> FastAPI:
    a = FakeConnector(ServiceId("fake_a"), "Fake A", ALL)
    a_tracks = {
        **{i: track("fake_a", i) for i in ("a", "b", "c")},
        "twin": track("fake_a", "twin", title="Twin song"),
    }
    a.add_playlist(
        snapshot(
            "fake_a", "P1", ["a", "b", "c", "twin"], name="Road Trip", tracks=a_tracks
        )
    )
    a.add_playlist(snapshot("fake_a", "P2", ["a", "b"], name="Focus", tracks=a_tracks))
    a.add_playlist(snapshot("fake_a", "P3", ["c"], name="Old Mix", tracks=a_tracks))

    b = FakeConnector(
        ServiceId("fake_b"),
        "Fake B",
        ALL - {Capability.REMOVE},
        catalog=[
            *(track("fake_b", i.upper(), title=i) for i in ("a", "b", "c")),
            track("fake_b", "T1", title="Twin song"),
            track("fake_b", "T2", title="Twin song"),
        ],
    )
    b.add_playlist(snapshot("fake_b", "B1", [], name="Existing"))

    c = FakeConnector(ServiceId("fake_c"), "Fake C", ALL)

    plugins = (StreamingFakePlugin(a), StreamingFakePlugin(b), FakePlugin(c))
    connectors = {p.service: p.connector for p in plugins}
    app = create_app(
        ServerConfig(
            admin_password=E2E_PASSWORD, data_dir=data_dir, public_url=public_url
        ),
        registry=PluginRegistry({p.service: p for p in plugins}, ()),
        start_worker=True,
        covers=StaticCovers(),
    )

    @app.post("/api/e2e/delete-playlist", status_code=204)
    def delete_playlist(body: DeletePlaylist) -> Response:
        connectors[ServiceId(body.service)].drop_playlist(body.playlist_id)
        return Response(status_code=204)

    return app


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="LinerSync e2e fake server")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--public-url", default="http://127.0.0.1:5173")
    args = parser.parse_args(argv)
    # uvicorn re-raises the signal it caught once it has shut down. With the default
    # SIGTERM action that kills the process before the folder below is removed,
    # so turn it into an exit.
    signal.signal(signal.SIGTERM, lambda _signum, _frame: sys.exit(0))
    # The data folder is removed when uvicorn returns.
    with tempfile.TemporaryDirectory(prefix="linersync-e2e-") as data:
        app = build_app(Path(data), args.public_url)
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=args.port,
            timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS,
        )


if __name__ == "__main__":
    main()
