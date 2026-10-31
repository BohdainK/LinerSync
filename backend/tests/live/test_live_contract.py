"""Opt-in contract run against the real services. Never part of the default suite.

Setup: python -m linersync.devtools.live_login --help. Requests and responses are
recorded (secrets redacted) under tests/fixtures/<service>/recorded/raw/.
"""

import asyncio
import os
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from linersync.adapters.connectors.apple_music.plugin import (
    APPLE_MUSIC_PLUGIN,
    AuthMode,
)
from linersync.adapters.connectors.jellyfin.parse import parse_track
from linersync.adapters.connectors.jellyfin.plugin import JELLYFIN_PLUGIN
from linersync.adapters.connectors.kit.plugin import ConnectorContext, ConnectorPlugin
from linersync.adapters.connectors.kit.recording import RecordingTransport
from linersync.adapters.connectors.qobuz.plugin import QOBUZ_PLUGIN
from linersync.adapters.connectors.spotify.plugin import SPOTIFY_PLUGIN
from linersync.adapters.connectors.tidal.plugin import TIDAL_PLUGIN
from linersync.adapters.connectors.youtube_music.plugin import (
    YOUTUBE_MUSIC_PLUGIN,
    YTAuthMode,
)
from linersync.adapters.memory.credentials import InMemoryCredentials
from linersync.adapters.memory.quota import InMemoryQuota
from linersync.domain.capabilities import Capability
from linersync.domain.operations import AddTracks, Removal, RemoveTracks
from linersync.domain.playlists import PlaylistDraft
from linersync.domain.tracks import ServiceId, Track, TrackQuery
from linersync.ports.connector import Connector
from linersync.testing.contract import (
    APPENDS,
    LAWS,
    ConnectorHarness,
    ContractSkip,
    Fault,
)
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


# Well-formed ids that name no playlist; Spotify answers 400 to a non-base62 id.
_MISSING_PLAYLIST_ID = {
    "spotify": "0000000000000000000000",
    "apple_music": "p.0000000000000",
    "jellyfin": "0" * 32,
    "tidal": "00000000-0000-0000-0000-000000000000",
    "youtube_music": "PL" + "0" * 32,
    "qobuz": "99999999999",
}


def _spotify_setup(env: Mapping[str, str]) -> tuple[ConnectorPlugin, dict[str, str]]:
    values = {
        "client_id": env["SPOTIFY_CLIENT_ID"],
        "refresh_token": env["SPOTIFY_REFRESH_TOKEN"],
    }
    return SPOTIFY_PLUGIN, values


def _apple_setup(env: Mapping[str, str]) -> tuple[ConnectorPlugin, dict[str, str]]:
    values = {"music_user_token": env["APPLE_MUSIC_USER_TOKEN"]}
    if APPLE_MUSIC_PLUGIN.mode is AuthMode.DEVELOPER:
        values |= {
            "team_id": env["APPLE_TEAM_ID"],
            "key_id": env["APPLE_KEY_ID"],
            "private_key": Path(env["APPLE_PRIVATE_KEY_PATH"]).read_text(),
        }
    return APPLE_MUSIC_PLUGIN, values


def _jellyfin_setup(env: Mapping[str, str]) -> tuple[ConnectorPlugin, dict[str, str]]:
    values = {
        "server_url": env["JELLYFIN_URL"],
        "api_key": env["JELLYFIN_API_KEY"],
        "username": env["JELLYFIN_USER"],
    }
    return JELLYFIN_PLUGIN, values


def _tidal_setup(env: Mapping[str, str]) -> tuple[ConnectorPlugin, dict[str, str]]:
    values = {
        "client_id": env["TIDAL_CLIENT_ID"],
        "refresh_token": env["TIDAL_REFRESH_TOKEN"],
    }
    return TIDAL_PLUGIN, values


def _youtube_setup(
    env: Mapping[str, str],
) -> tuple[ConnectorPlugin, dict[str, str]]:
    if YOUTUBE_MUSIC_PLUGIN.mode is YTAuthMode.OAUTH:
        values = {
            "client_id": env["YTMUSIC_CLIENT_ID"],
            "client_secret": env["YTMUSIC_CLIENT_SECRET"],
            "refresh_token": env["YTMUSIC_REFRESH_TOKEN"],
        }
    else:
        values = {"cookie": env["YTMUSIC_COOKIE"]}
    return YOUTUBE_MUSIC_PLUGIN, values


def _qobuz_setup(env: Mapping[str, str]) -> tuple[ConnectorPlugin, dict[str, str]]:
    values = {
        "app_id": env["QOBUZ_APP_ID"],
        "user_auth_token": env["QOBUZ_USER_AUTH_TOKEN"],
    }
    return QOBUZ_PLUGIN, values


_LIVE_SETUP: dict[
    str, Callable[[Mapping[str, str]], tuple[ConnectorPlugin, dict[str, str]]]
] = {
    "spotify": _spotify_setup,
    "apple_music": _apple_setup,
    "jellyfin": _jellyfin_setup,
    "tidal": _tidal_setup,
    "youtube_music": _youtube_setup,
    "qobuz": _qobuz_setup,
}


_LIST_WAIT_SECONDS = 30
_TITLE = "LinerSync live test"
# Apple can't remove songs, so the appends playlist only grows; start a new
# one past this size.
_APPENDS_MAX = 2000


class LiveHarness:
    def __init__(self, service: str) -> None:
        self._count = 0
        self._titled: dict[str, str] = {}
        self.missing_playlist_id = _MISSING_PLAYLIST_ID[service]
        env = os.environ
        credentials = InMemoryCredentials()
        plugin, values = _LIVE_SETUP[service](env)
        credentials.save(ServiceId(service), values)
        self._service = service
        self._env = env
        transport = RecordingTransport(
            httpx.AsyncHTTPTransport(), _FIXTURES / service / "recorded" / "raw"
        )
        ctx = ConnectorContext(
            credentials, InMemoryQuota(), _SystemClock(), transport=transport
        )
        self.connector: Connector = plugin.create(ctx)
        self.catalog: tuple[Track, ...] = ()

    async def load_catalog(self) -> None:
        if self._service == "jellyfin":
            # Whatever is in the library: Jellyfin has no ISRCs to look up.
            body = await self._jellyfin_get(
                "/Items",
                {
                    "IncludeItemTypes": "Audio",
                    "Recursive": "true",
                    "Limit": 3,
                    "Fields": "ProviderIds",
                },
            )
            base = self._env["JELLYFIN_URL"].rstrip("/")
            songs = [parse_track(item, base) for item in body["Items"]]
            self.catalog = tuple(t for t in songs if t is not None)
            assert len(self.catalog) == 3, "the Jellyfin library needs 3 songs"
            return
        found: list[Track] = []
        for title, artist in _SONGS:
            hits = await self.connector.search(TrackQuery(title, (artist,)))
            # YouTube Music never returns ISRCs: its top hit has to do.
            no_isrcs = self._service == "youtube_music"
            top = next((t for t in hits if t.isrc or no_isrcs), None)
            assert top is not None, f"no hit with an ISRC for {title!r}"
            found.append(top)
        self.catalog = tuple(found)

    async def make_playlist(self, name: str, track_ids: Sequence[str]) -> str:
        """Reuses the playlist titled after name, from this run or an earlier
        one, whenever the contract allows it (ConnectorHarness.make_playlist),
        so runs leave as few playlists behind as possible."""
        title = f"{_TITLE} - {name}"
        wanted = tuple(track_ids)
        pid = await self._find(title)
        if pid is not None:
            current = (await self.connector.read_playlist(pid)).track_ids
            if name == APPENDS and len(current) < _APPENDS_MAX:
                return pid
            if current == wanted:
                return pid
            if Capability.REMOVE in self.connector.capabilities:
                await self._reset(pid, current, wanted)
                return pid
        pid = await self._create(title, wanted)
        self._titled[title] = pid
        return pid

    async def _find(self, title: str) -> str | None:
        if title not in self._titled:
            for meta in await self.connector.list_playlists():
                self._titled.setdefault(meta.name, meta.playlist_id)
        return self._titled.get(title)

    def _op_id(self) -> str:
        self._count += 1
        return f"live-{self._count}"

    async def _reset(
        self, pid: str, current: tuple[str, ...], wanted: tuple[str, ...]
    ) -> None:
        if current:
            removals = tuple(Removal(t, i) for i, t in enumerate(current))
            await self.connector.apply(pid, RemoveTracks(self._op_id(), removals))
        if wanted:
            await self.connector.apply(pid, AddTracks(self._op_id(), wanted))

    async def _create(self, title: str, wanted: tuple[str, ...]) -> str:
        if self._service == "jellyfin":
            return await self._jellyfin_create(title, wanted)
        meta = await self.connector.create_playlist(PlaylistDraft(title))
        print(f"created playlist {title!r}")
        if wanted:
            await self.connector.apply(
                meta.playlist_id, AddTracks(self._op_id(), wanted)
            )
        await self._wait_until_listed(meta.playlist_id)
        return meta.playlist_id

    async def _wait_until_listed(self, playlist_id: str) -> None:
        """Apple's library listing lags a few seconds behind a create (seen live:
        missing after 2 s, present after 6 s); an arranged playlist should be
        settled before a law looks at it."""
        for attempt in range(_LIST_WAIT_SECONDS):
            listed = await self.connector.list_playlists()
            if playlist_id in {m.playlist_id for m in listed}:
                if attempt:
                    print(f"{playlist_id} listed after ~{attempt} s")
                return
            await asyncio.sleep(1)
        raise AssertionError(f"{playlist_id} not listed after {_LIST_WAIT_SECONDS} s")

    # -- Jellyfin is read-only: arrange its playlists out of band ---------

    async def _jellyfin_get(
        self, path: str, params: dict[str, str | int]
    ) -> dict[str, Any]:
        async with self._jellyfin_client() as client:
            user_id = await self._jellyfin_user_id(client)
            response = await client.get(path, params={"userId": user_id, **params})
            response.raise_for_status()
            body: dict[str, Any] = response.json()
            return body

    async def _jellyfin_create(self, title: str, wanted: tuple[str, ...]) -> str:
        async with self._jellyfin_client() as client:
            user_id = await self._jellyfin_user_id(client)
            response = await client.post(
                "/Playlists",
                json={"Name": title, "Ids": list(wanted), "UserId": user_id},
            )
            response.raise_for_status()
        print(f"created playlist {title!r}")
        pid = str(response.json()["Id"])
        await self._wait_until_listed(pid)
        return pid

    def _jellyfin_client(self) -> httpx.AsyncClient:
        token = self._env["JELLYFIN_API_KEY"]
        return httpx.AsyncClient(
            base_url=self._env["JELLYFIN_URL"].rstrip("/"),
            headers={"Authorization": f'MediaBrowser Token="{token}"'},
        )

    async def _jellyfin_user_id(self, client: httpx.AsyncClient) -> str:
        response = await client.get("/Users")
        response.raise_for_status()
        wanted = self._env["JELLYFIN_USER"].casefold()
        for user in response.json():
            if str(user["Name"]).casefold() == wanted:
                return str(user["Id"])
        raise AssertionError(f"no Jellyfin user {self._env['JELLYFIN_USER']!r}")

    def supports(self, fault: Fault) -> bool:
        return False

    def inject(self, fault: Fault, playlist_id: str) -> None:
        raise ContractSkip("live harness injects no faults")


@pytest.fixture(
    params=("spotify", "apple_music", "jellyfin", "tidal", "youtube_music", "qobuz")
)
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
