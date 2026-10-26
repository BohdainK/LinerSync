"""Opt-in contract run against the real services. Never part of the default suite.

Setup: python -m linersync.devtools.live_login --help. Requests and responses are
recorded (secrets redacted) under tests/fixtures/<service>/recorded/raw/.
"""

import os
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from linersync.adapters.connectors.apple_music.plugin import APPLE_MUSIC_PLUGIN
from linersync.adapters.connectors.kit.plugin import ConnectorContext, ConnectorPlugin
from linersync.adapters.connectors.kit.recording import RecordingTransport
from linersync.adapters.connectors.spotify.plugin import SPOTIFY_PLUGIN
from linersync.adapters.memory.credentials import InMemoryCredentials
from linersync.adapters.memory.quota import InMemoryQuota
from linersync.domain.operations import AddTracks
from linersync.domain.playlists import PlaylistDraft
from linersync.domain.tracks import ServiceId, Track, TrackQuery
from linersync.ports.connector import Connector
from linersync.testing.contract import LAWS, ConnectorHarness, ContractSkip, Fault
from tests.live.conftest import live_skip_reason

pytestmark = pytest.mark.live

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_SONGS = (
    ("Bohemian Rhapsody", "Queen"),
    ("Billie Jean", "Michael Jackson"),
    ("Smells Like Teen Spirit", "Nirvana"),
)


class _SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class LiveHarness:
    def __init__(self, service: str) -> None:
        self._count = 0
        env = os.environ
        credentials = InMemoryCredentials()
        plugin: ConnectorPlugin
        if service == "spotify":
            plugin = SPOTIFY_PLUGIN
            credentials.save(
                ServiceId("spotify"),
                {
                    "client_id": env["SPOTIFY_CLIENT_ID"],
                    "refresh_token": env["SPOTIFY_REFRESH_TOKEN"],
                },
            )
        else:
            plugin = APPLE_MUSIC_PLUGIN
            credentials.save(
                ServiceId("apple_music"),
                {
                    "team_id": env["APPLE_TEAM_ID"],
                    "key_id": env["APPLE_KEY_ID"],
                    "private_key": Path(env["APPLE_PRIVATE_KEY_PATH"]).read_text(),
                    "music_user_token": env["APPLE_MUSIC_USER_TOKEN"],
                },
            )
        transport = RecordingTransport(
            httpx.AsyncHTTPTransport(), _FIXTURES / service / "recorded" / "raw"
        )
        ctx = ConnectorContext(
            credentials, InMemoryQuota(), _SystemClock(), transport=transport
        )
        self.connector: Connector = plugin.create(ctx)
        self.catalog: tuple[Track, ...] = ()

    async def load_catalog(self) -> None:
        found: list[Track] = []
        for title, artist in _SONGS:
            hits = await self.connector.search(TrackQuery(title, (artist,)))
            top = next((t for t in hits if t.isrc), None)
            assert top is not None, f"no hit with an ISRC for {title!r}"
            found.append(top)
        self.catalog = tuple(found)

    async def make_playlist(self, name: str, track_ids: Sequence[str]) -> str:
        stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
        draft = PlaylistDraft(f"LinerSync live test {stamp}")
        meta = await self.connector.create_playlist(draft)
        self._count += 1
        if track_ids:
            op = AddTracks(f"live-{self._count}", tuple(track_ids))
            await self.connector.apply(meta.playlist_id, op)
        return meta.playlist_id

    def supports(self, fault: Fault) -> bool:
        return False

    def inject(self, fault: Fault, playlist_id: str) -> None:
        raise ContractSkip("live harness injects no faults")


@pytest.fixture(params=("spotify", "apple_music"))
def service(request: pytest.FixtureRequest) -> str:
    name: str = request.param
    reason = live_skip_reason(name, os.environ)
    if reason is not None:
        pytest.skip(reason)
    return name


async def test_live_contract(service: str) -> None:
    harness = LiveHarness(service)
    await harness.load_catalog()
    typed: ConnectorHarness = harness
    for name, law in sorted(LAWS.items()):
        try:
            await law(typed)
        except ContractSkip as skip:
            print(f"{service}: {name} skipped: {skip}")
