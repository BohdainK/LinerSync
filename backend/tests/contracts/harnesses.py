import re
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime

from linersync.adapters.connectors.apple_music.plugin import APPLE_MUSIC_PLUGIN
from linersync.adapters.connectors.kit.http import RetryPolicy
from linersync.adapters.connectors.kit.plugin import ConnectorContext
from linersync.adapters.connectors.spotify.plugin import SPOTIFY_PLUGIN
from linersync.adapters.fake.connector import FakeConnector
from linersync.adapters.memory.credentials import InMemoryCredentials
from linersync.adapters.memory.quota import InMemoryQuota
from linersync.adapters.memory.runtime import FixedClock
from linersync.domain.capabilities import Capability
from linersync.domain.operations import AddTracks
from linersync.domain.tracks import ServiceId
from linersync.ports.connector import Connector
from linersync.testing.contract import ConnectorHarness, Fault
from tests.adapters.connectors.apple_music.sim import AppleMusicSim
from tests.adapters.connectors.spotify.sim import SpotifySim
from tests.factories import snapshot, track

SVC = ServiceId("fake")


class FakeHarness:
    def __init__(self) -> None:
        self.catalog = tuple(
            track(
                "fake",
                f"fake-{name}",
                title=name.title(),
                isrc=f"ISRC{i:08d}",
            )
            for i, name in enumerate(("alpha", "bravo", "charlie"))
        )
        self.fake = FakeConnector(
            SVC, "Fake", frozenset(Capability), catalog=self.catalog
        )
        self.connector: Connector = self.fake
        self._count = 0

    async def make_playlist(self, name: str, track_ids: Sequence[str]) -> str:
        self._count += 1
        pid = f"fake-arranged-{self._count}"
        known = {t.track_id: t for t in self.catalog}
        self.fake.add_playlist(
            snapshot("fake", pid, track_ids, name=name, tracks=known)
        )
        return pid

    def supports(self, fault: Fault) -> bool:
        return True

    def inject(self, fault: Fault, playlist_id: str) -> None:
        match fault:
            case Fault.READ_ERROR:
                self.fake.fail_reads(playlist_id)
            case Fault.TRUNCATED_READ:
                self.fake.truncate_reads(playlist_id)
            case Fault.WRITE_REJECTED:
                self.fake.fail_writes(AddTracks, applied=False)
            case Fault.WRITE_MAYBE_APPLIED:
                self.fake.fail_writes(AddTracks, applied=True)


class SpotifyHarness:
    def __init__(self) -> None:
        self.catalog = tuple(
            track(
                "spotify",
                f"spotify:track:{name * 4}{i:06d}",
                title=name.title(),
                artists=(f"{name.title()} Band",),
                isrc=f"USSIM{i:07d}",
            )
            for i, name in enumerate(("alpha", "bravo", "charlie"))
        )
        self.sim = SpotifySim(catalog=self.catalog)
        store = InMemoryCredentials()
        store.save(
            ServiceId("spotify"),
            {
                "client_id": "a" * 32,
                "access_token": self.sim.access_token,
                "refresh_token": "ref",
                "expires_at": "2026-10-26T13:00:00+00:00",
            },
        )

        async def no_sleep(_: float) -> None:
            return None

        ctx = ConnectorContext(
            store,
            InMemoryQuota(),
            FixedClock(datetime(2026, 10, 26, 12, tzinfo=UTC)),
            transport=self.sim.transport,
            sleep=no_sleep,
        )
        self.connector: Connector = SPOTIFY_PLUGIN.create(ctx)

    async def make_playlist(self, name: str, track_ids: Sequence[str]) -> str:
        return self.sim.add_playlist(name, track_ids)

    def supports(self, fault: Fault) -> bool:
        return True

    def inject(self, fault: Fault, playlist_id: str) -> None:
        items = rf"/playlists/{re.escape(playlist_id)}/items"
        match fault:
            case Fault.READ_ERROR:
                # Every attempt the HTTP layer makes fails.
                times = RetryPolicy().max_attempts
                self.sim.fail_next("GET", items, 500, times=times)
            case Fault.TRUNCATED_READ:
                self.sim.truncate(playlist_id)
            case Fault.WRITE_REJECTED:
                self.sim.fail_next("POST", items, 403, apply_first=False)
            case Fault.WRITE_MAYBE_APPLIED:
                self.sim.timeout_next("POST", items, apply_first=True)


class AppleMusicHarness:
    def __init__(self) -> None:
        self.catalog = tuple(
            track(
                "apple_music",
                str(1_000_000_000 + i),
                title=name.title(),
                artists=(f"{name.title()} Band",),
                isrc=f"USSIM{i:07d}",
            )
            for i, name in enumerate(("alpha", "bravo", "charlie"))
        )
        self.sim = AppleMusicSim(catalog=self.catalog)
        store = InMemoryCredentials()
        store.save(ServiceId("apple_music"), self.sim.credentials())

        async def no_sleep(_: float) -> None:
            return None

        ctx = ConnectorContext(
            store,
            InMemoryQuota(),
            FixedClock(datetime(2026, 10, 26, 12, tzinfo=UTC)),
            transport=self.sim.transport,
            sleep=no_sleep,
        )
        self.connector: Connector = APPLE_MUSIC_PLUGIN.create(ctx)

    async def make_playlist(self, name: str, track_ids: Sequence[str]) -> str:
        return self.sim.add_playlist(name, track_ids)

    def supports(self, fault: Fault) -> bool:
        return True

    def inject(self, fault: Fault, playlist_id: str) -> None:
        tracks = rf"/v1/me/library/playlists/{re.escape(playlist_id)}/tracks"
        match fault:
            case Fault.READ_ERROR:
                # Every attempt the HTTP layer makes fails.
                times = RetryPolicy().max_attempts
                self.sim.fail_next("GET", tracks, 500, times=times)
            case Fault.TRUNCATED_READ:
                self.sim.truncate(playlist_id)
            case Fault.WRITE_REJECTED:
                # Only the POST: the add's baseline GET must still succeed.
                self.sim.fail_next("POST", tracks, 403, apply_first=False)
            case Fault.WRITE_MAYBE_APPLIED:
                self.sim.timeout_next("POST", tracks, apply_first=True)


async def _fake() -> ConnectorHarness:
    return FakeHarness()


async def _spotify() -> ConnectorHarness:
    return SpotifyHarness()


async def _apple_music() -> ConnectorHarness:
    return AppleMusicHarness()


HARNESSES: dict[str, Callable[[], Awaitable[ConnectorHarness]]] = {
    "fake": _fake,
    "spotify": _spotify,
    "apple_music": _apple_music,
}
