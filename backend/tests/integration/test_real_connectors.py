"""End-to-end runs: the Plan 1 engine with the real Spotify, Apple Music and
Jellyfin connectors, each talking to its simulator."""

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

import httpx

from linersync.adapters.connectors.apple_music.plugin import (
    PLUGIN as APPLE_PLUGIN,
)
from linersync.adapters.connectors.jellyfin.plugin import JELLYFIN_PLUGIN
from linersync.adapters.connectors.kit.discovery import discover_plugins
from linersync.adapters.connectors.kit.plugin import ConnectorContext, ConnectorSettings
from linersync.adapters.connectors.qobuz.plugin import QOBUZ_PLUGIN
from linersync.adapters.connectors.spotify.plugin import PLUGIN as SPOTIFY_PLUGIN
from linersync.adapters.connectors.tidal.plugin import TIDAL_PLUGIN
from linersync.adapters.connectors.youtube_music.plugin import (
    YouTubeMusicPlugin,
    YTAuthMode,
)
from linersync.adapters.memory.credentials import InMemoryCredentials
from linersync.adapters.memory.quota import InMemoryQuota
from linersync.adapters.memory.repositories import in_memory_repositories
from linersync.adapters.memory.runtime import FixedClock, RecordingEvents, SequentialIds
from linersync.application.attention import AttentionService
from linersync.application.breaker import CircuitBreaker
from linersync.application.config import EngineConfig
from linersync.application.runner import Deps, SyncRunner
from linersync.domain.attention import AttentionKind
from linersync.domain.runs import RunStatus, SyncRun, Trigger
from linersync.domain.sync import SyncMode
from linersync.domain.tracks import ServiceId, Track
from linersync.ports.repositories import Repositories
from tests.adapters.connectors.apple_music.sim import AppleMusicSim
from tests.adapters.connectors.jellyfin.sim import JellyfinSim
from tests.adapters.connectors.qobuz.sim import QobuzSim
from tests.adapters.connectors.spotify.sim import ART_PREFIX, SpotifySim
from tests.adapters.connectors.tidal.sim import TidalSim
from tests.adapters.connectors.youtube_music.sim import YouTubeMusicSim
from tests.factories import group, track

SP = ServiceId("spotify")
AM = ServiceId("apple_music")
JF = ServiceId("jellyfin")
TD = ServiceId("tidal")
YT = ServiceId("youtube_music")
QB = ServiceId("qobuz")
NOW = datetime(2026, 10, 26, 12, tzinfo=UTC)
APPLE_TRACKS = r"/v1/me/library/playlists/[^/]+/tracks"
SPOTIFY_ITEMS = r"/playlists/[^/]+/items"
_WRITES = {"POST", "PUT", "DELETE"}


def catalogs(n: int) -> tuple[tuple[Track, ...], tuple[Track, ...]]:
    """`n` songs on Spotify and the same songs (by ISRC) in Apple's catalog."""
    spotify = tuple(
        track(
            "spotify",
            f"spotify:track:{i:022d}",
            title=f"Song {i}",
            artists=(f"Artist {i}",),
            isrc=f"USINT{i:07d}",
        )
        for i in range(n)
    )
    apple = tuple(
        track(
            "apple_music",
            str(3_000_000_000 + i),
            title=f"Song {i}",
            artists=(f"Artist {i}",),
            isrc=f"USINT{i:07d}",
        )
        for i in range(n)
    )
    return spotify, apple


def writes(requests: Sequence[httpx.Request], host: str) -> list[httpx.Request]:
    return [r for r in requests if r.url.host == host and r.method in _WRITES]


def apple_ids(apple: Sequence[Track]) -> list[str]:
    return [t.track_id for t in apple]


@dataclass
class Rig:
    spotify: SpotifySim
    apple: AppleMusicSim
    spotify_expires_at: datetime = NOW + timedelta(hours=1)
    repos: Repositories = field(default_factory=in_memory_repositories)
    clock: FixedClock = field(default_factory=lambda: FixedClock(NOW))
    sleeps: list[float] = field(default_factory=list)
    transports: dict[str, httpx.AsyncBaseTransport] = field(default_factory=dict)

    async def _sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    def _context(
        self, credentials: InMemoryCredentials, transport: httpx.AsyncBaseTransport
    ) -> ConnectorContext:
        return ConnectorContext(
            credentials,
            InMemoryQuota(),
            self.clock,
            settings=ConnectorSettings(hash_artwork=False),
            transport=transport,
            sleep=self._sleep,
        )

    def runner(self) -> SyncRunner:
        creds = InMemoryCredentials()
        creds.save(
            SP,
            {
                "client_id": "a" * 32,
                "access_token": self.spotify.access_token,
                "refresh_token": "ref",
                "expires_at": self.spotify_expires_at.isoformat(),
            },
        )
        creds.save(AM, self.apple.credentials(storefront="us"))
        spotify = SPOTIFY_PLUGIN.create(
            self._context(creds, self.transports.get("spotify", self.spotify.transport))
        )
        apple = APPLE_PLUGIN.create(
            self._context(creds, self.transports.get("apple", self.apple.transport))
        )
        return SyncRunner(
            Deps(
                connectors={SP: spotify, AM: apple},
                repos=self.repos,
                clock=self.clock,
                events=RecordingEvents(),
                new_id=SequentialIds("id"),
                breaker=CircuitBreaker(
                    self.repos.breakers, self.clock, EngineConfig().breaker
                ),
            )
        )

    def to_apple(self, source_id: str) -> str:
        g = group(
            source=("spotify", source_id),
            destinations=[("apple_music", None, SyncMode.ADD_ONLY)],
        )
        self.repos.syncs.save(g)
        return g.sync_id

    def to_spotify(
        self, source_id: str, target_id: str, *, mirror: bool = False
    ) -> str:
        g = group(
            source=("spotify", source_id),
            destinations=[("spotify", target_id, SyncMode.FULL)],
        )
        if mirror:
            g = g.with_member(replace(g.destinations[0], mirror_once=True))
        self.repos.syncs.save(g)
        return g.sync_id

    async def run(self, sync_id: str) -> SyncRun:
        return await self.runner().run(sync_id, trigger=Trigger.MANUAL)

    def apple_playlist(self, sync_id: str) -> str:
        saved = self.repos.syncs.get(sync_id)
        assert saved is not None
        pid = saved.destinations[0].playlist_id
        assert pid is not None
        return pid


def rig(n: int) -> tuple[Rig, str, tuple[Track, ...]]:
    """A rig whose Spotify P1 holds the first `n` songs, in order."""
    sp_catalog, am_catalog = catalogs(n)
    sim = SpotifySim(catalog=sp_catalog)
    p1 = sim.add_playlist("Mix", [t.track_id for t in sp_catalog])
    return Rig(sim, AppleMusicSim(catalog=am_catalog)), p1, am_catalog


class FailingFrom:
    """A transport that lets the first n-1 matching requests through and fails
    every later one with `status` (they do not land), until `heal()`."""

    def __init__(
        self, sim: AppleMusicSim, method: str, path_regex: str, n: int, status: int
    ) -> None:
        self._sim = sim
        self._method = method
        self._pattern = re.compile(path_regex)
        self._n = n
        self._status = status
        self._seen = 0
        self.failed = 0
        self.healthy = False
        self.transport = httpx.MockTransport(self._handle)

    def heal(self) -> None:
        self.healthy = True

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if request.method == self._method and self._pattern.fullmatch(request.url.path):
            self._seen += 1
            if self._seen >= self._n and not self.healthy:
                self.failed += 1
                return httpx.Response(self._status, json={"errors": []})
        return self._sim.handle(request)


# -- discovery ---------------------------------------------------------------


def test_builtin_plugins_discovered() -> None:
    registry = discover_plugins()
    assert registry.errors == ()
    assert {SP, AM, JF, TD, YT, QB} <= set(registry.plugins)


# -- Spotify → Apple Music ---------------------------------------------------


async def test_first_sync_creates_apple_playlist_and_matches_by_isrc() -> None:
    r, p1, apple = rig(3)
    sync_id = r.to_apple(p1)
    run = await r.run(sync_id)
    assert run.status is RunStatus.SUCCEEDED, run
    pid = r.apple_playlist(sync_id)
    assert r.apple.ids(pid) == apple_ids(apple)
    lookups = [q for q in r.apple.requests if "filter[isrc]" in q.url.params]
    assert len(lookups) == 3
    assert not [q for q in r.apple.requests if q.url.path.endswith("/search")]


async def test_apple_failure_midway_never_touches_spotify_and_rerun_completes() -> None:
    r, p1, apple = rig(250)
    flaky = FailingFrom(r.apple, "POST", APPLE_TRACKS, 2, 500)
    r.transports["apple"] = flaky.transport
    sync_id = r.to_apple(p1)
    run = await r.run(sync_id)
    # Failed midway, not held by the brake (AWAITING_APPROVAL) or partial.
    assert run.status is not RunStatus.AWAITING_APPROVAL, run
    assert run.status is RunStatus.FAILED, run
    assert flaky.failed >= 1
    assert writes(r.spotify.requests, "api.spotify.com") == []
    assert r.spotify.uris(p1) == [
        f"spotify:track:{i:022d}" for i in range(250)
    ]  # the source playlist is unchanged

    (member,) = run.members
    created = member.created_playlist_id
    assert created is not None and r.apple_playlist(sync_id) == created
    assert len(r.apple.ids(created)) == 100  # the first batch only

    flaky.heal()
    rerun = await r.run(sync_id)
    assert rerun.status is RunStatus.SUCCEEDED, rerun
    assert r.apple.ids(r.apple_playlist(sync_id)) == apple_ids(apple)


async def test_apple_timeout_after_landing_no_duplicates() -> None:
    r, p1, apple = rig(3)
    r.apple.timeout_next("POST", APPLE_TRACKS, apply_first=True)
    sync_id = r.to_apple(p1)
    run = await r.run(sync_id)
    assert run.status is RunStatus.SUCCEEDED, run
    (post,) = writes(
        [q for q in r.apple.requests if q.url.path.endswith("/tracks")],
        "api.music.apple.com",
    )  # the timed-out POST landed and was neither retried nor re-sent
    ids = r.apple.ids(r.apple_playlist(sync_id))
    assert Counter(ids) == Counter(apple_ids(apple))


async def test_apple_timeout_after_landing_with_lag_no_duplicates() -> None:
    # An existing, non-empty Apple playlist already holds songs 0 and 1; the
    # source adds songs 2 and 3. The add lands, then times out, while Apple's
    # library still shows the old songs.
    r, p1, apple = rig(4)
    ids = apple_ids(apple)
    target = r.apple.add_playlist("Mix", ids[:2])
    r.apple.lag_reads = 3
    r.apple.timeout_next("POST", APPLE_TRACKS, apply_first=True)
    g = group(
        source=("spotify", p1),
        destinations=[("apple_music", target, SyncMode.ADD_ONLY)],
    )
    r.repos.syncs.save(g)
    await r.run(g.sync_id)
    assert Counter(r.apple.ids(target)) == Counter(ids)


async def test_apple_lag_no_duplicates() -> None:
    r, p1, apple = rig(3)
    r.apple.lag_reads = 3
    sync_id = r.to_apple(p1)
    run = await r.run(sync_id)
    assert run.status is RunStatus.SUCCEEDED, run
    assert r.sleeps  # the connector waited for Apple's library to catch up
    ids = r.apple.ids(r.apple_playlist(sync_id))
    assert Counter(ids) == Counter(apple_ids(apple))
    kinds = {item.kind for item in r.repos.attention.list_open()}
    assert AttentionKind.VERIFY_MISMATCH not in kinds


async def test_spotify_read_error_aborts_without_writes() -> None:
    r, p1, _ = rig(3)
    r.spotify.fail_next("GET", SPOTIFY_ITEMS, 500, times=4)
    run = await r.run(r.to_apple(p1))
    assert run.status in (RunStatus.ABORTED, RunStatus.FAILED)
    assert writes(r.apple.requests, "api.music.apple.com") == []


async def test_expired_spotify_login_aborts_without_writes() -> None:
    r, p1, _ = rig(3)
    r.spotify.expire_login()
    r.spotify_expires_at = NOW - timedelta(hours=1)
    run = await r.run(r.to_apple(p1))
    assert run.status in (RunStatus.ABORTED, RunStatus.FAILED)
    assert run.error is not None and "sign in again" in run.error.lower()
    assert writes(r.apple.requests, "api.music.apple.com") == []


# -- Spotify → Spotify -------------------------------------------------------


async def test_spotify_full_sync_removes_one_copy() -> None:
    sp_catalog, _ = catalogs(3)
    a, b, c = (t.track_id for t in sp_catalog)
    sim = SpotifySim(catalog=sp_catalog)
    source = sim.add_playlist("Mix", [a, b, c])
    target = sim.add_playlist("Mix", [a, b, a, c])
    r = Rig(sim, AppleMusicSim(catalog=()))
    # A strict first mirror: the extra copy predates the sync.
    sync_id = r.to_spotify(source, target, mirror=True)
    # Removing 1 of 4 songs trips the removal brake: nothing is written until
    # the user approves this exact plan.
    held = await r.run(sync_id)
    assert held.status is RunStatus.AWAITING_APPROVAL, held
    assert writes(sim.requests, "api.spotify.com") == []
    (brake,) = [
        i for i in r.repos.attention.list_open() if i.kind is AttentionKind.BRAKE
    ]
    AttentionService(r.repos, r.clock).approve(brake.item_id)

    run = await r.run(sync_id)
    assert run.status is RunStatus.SUCCEEDED, run
    assert sim.uris(target) == [a, b, c]
    (member,) = run.members
    assert member.mismatch is None
    assert member.verified_after == (a, b, c)


async def test_metadata_stable_across_runs() -> None:
    sp_catalog, _ = catalogs(2)
    uris = [t.track_id for t in sp_catalog]
    sim = SpotifySim(catalog=sp_catalog)
    source = sim.add_playlist(
        "Mix", uris, description="Rock & roll", image_url=f"{ART_PREFIX}cover"
    )
    target = sim.add_playlist("Mix", [])
    r = Rig(sim, AppleMusicSim(catalog=()))
    sync_id = r.to_spotify(source, target)
    first = await r.run(sync_id)
    assert first.status is RunStatus.SUCCEEDED, first
    # Run 1 did copy the details: otherwise run 2 proves nothing.
    copied = sim.playlists[target]
    assert copied.description == "Rock & roll"  # sent unescaped
    assert copied.image_url is not None
    puts = [
        q
        for q in sim.requests
        if q.method == "PUT" and q.url.path.endswith(f"/playlists/{target}")
    ]
    assert len(puts) == 1
    uploads = [
        q for q in sim.requests if q.url.path.endswith(f"/playlists/{target}/images")
    ]
    assert len(uploads) == 1

    before = len(sim.requests)
    second = await r.run(sync_id)
    assert second.status is RunStatus.SUCCEEDED, second
    later = sim.requests[before:]
    assert [q for q in later if q.method == "PUT" and q.url.path.endswith(target)] == []
    assert [q for q in later if q.url.path.endswith("/images")] == []


async def test_dead_source_cover_does_not_block_sync() -> None:
    sp_catalog, _ = catalogs(2)
    uris = [t.track_id for t in sp_catalog]
    sim = SpotifySim(catalog=sp_catalog)
    source = sim.add_playlist(
        "Mix", uris, description="d", image_url="https://covers.example/gone.jpg"
    )
    target = sim.add_playlist("Old", [])
    r = Rig(sim, AppleMusicSim(catalog=()))
    run = await r.run(r.to_spotify(source, target))
    assert run.status is RunStatus.SUCCEEDED, run
    assert sim.uris(target) == uris
    assert (sim.playlists[target].name, sim.playlists[target].description) == (
        "Mix",
        "d",
    )
    assert [q for q in sim.requests if q.url.path.endswith("/images")] == []


async def test_empty_source_creates_apple_playlist_only_once_it_has_songs() -> None:
    sp_catalog, am_catalog = catalogs(2)
    sim = SpotifySim(catalog=sp_catalog)
    p1 = sim.add_playlist("Mix", [])
    r = Rig(sim, AppleMusicSim(catalog=am_catalog))
    sync_id = r.to_apple(p1)
    first = await r.run(sync_id)
    assert first.status is not RunStatus.FAILED, first
    assert r.apple.playlists == {}
    assert writes(r.apple.requests, "api.music.apple.com") == []

    sim.playlists[p1].uris.extend(t.track_id for t in sp_catalog)
    second = await r.run(sync_id)
    assert second.status is RunStatus.SUCCEEDED, second
    assert r.apple.ids(r.apple_playlist(sync_id)) == apple_ids(am_catalog)


# -- Jellyfin → Spotify ------------------------------------------------------


async def test_jellyfin_to_spotify_sync_matches_without_isrc() -> None:
    """Jellyfin has no ISRCs: songs are matched on title, artist and length."""
    songs = [("Alpha", "Alpha Band"), ("Bravo", "Bravo Band"), ("Charlie", "C Band")]
    jf_catalog = tuple(
        track("jellyfin", f"{i + 1:032x}", title=t, artists=(a,))
        for i, (t, a) in enumerate(songs)
    )
    sp_catalog = tuple(
        track(
            "spotify",
            f"spotify:track:{i:022d}",
            title=t,
            artists=(a,),
            isrc=f"USJF0{i:07d}",
        )
        for i, (t, a) in enumerate(songs)
    )
    jellyfin, spotify = JellyfinSim(catalog=jf_catalog), SpotifySim(catalog=sp_catalog)
    source = jellyfin.add_playlist("Mix", [t.track_id for t in jf_catalog])
    creds = InMemoryCredentials()
    creds.save(JF, jellyfin.credentials())
    creds.save(
        SP,
        {
            "client_id": "a" * 32,
            "access_token": spotify.access_token,
            "refresh_token": "ref",
            "expires_at": (NOW + timedelta(hours=1)).isoformat(),
        },
    )
    clock = FixedClock(NOW)

    async def no_sleep(_: float) -> None:
        return None

    def ctx(transport: httpx.AsyncBaseTransport) -> ConnectorContext:
        return ConnectorContext(
            creds,
            InMemoryQuota(),
            clock,
            settings=ConnectorSettings(hash_artwork=False),
            transport=transport,
            sleep=no_sleep,
        )

    repos = in_memory_repositories()
    runner = SyncRunner(
        Deps(
            connectors={
                JF: JELLYFIN_PLUGIN.create(ctx(jellyfin.transport)),
                SP: SPOTIFY_PLUGIN.create(ctx(spotify.transport)),
            },
            repos=repos,
            clock=clock,
            events=RecordingEvents(),
            new_id=SequentialIds("id"),
            breaker=CircuitBreaker(repos.breakers, clock, EngineConfig().breaker),
        )
    )
    g = group(
        source=("jellyfin", source),
        destinations=[("spotify", None, SyncMode.FULL)],
    )
    repos.syncs.save(g)
    run = await runner.run(g.sync_id, trigger=Trigger.MANUAL)
    assert run.status is RunStatus.SUCCEEDED, run
    saved = repos.syncs.get(g.sync_id)
    assert saved is not None
    target = saved.destinations[0].playlist_id
    assert target is not None
    assert spotify.uris(target) == [t.track_id for t in sp_catalog]


# -- Spotify → Tidal -----------------------------------------------------------


async def test_spotify_to_tidal_full_sync_creates_then_removes() -> None:
    sp_catalog, _ = catalogs(3)
    td_catalog = tuple(
        track(
            "tidal",
            str(80_000_000 + i),
            title=t.title,
            artists=t.artists,
            isrc=t.isrc,
        )
        for i, t in enumerate(sp_catalog)
    )
    a, b, c = (t.track_id for t in sp_catalog)
    ta, tb, tc = (t.track_id for t in td_catalog)
    spotify, tidal = SpotifySim(catalog=sp_catalog), TidalSim(catalog=td_catalog)
    source = spotify.add_playlist("Mix", [a, b, c])
    creds = InMemoryCredentials()
    creds.save(TD, tidal.credentials())
    creds.save(
        SP,
        {
            "client_id": "a" * 32,
            "access_token": spotify.access_token,
            "refresh_token": "ref",
            "expires_at": (NOW + timedelta(hours=1)).isoformat(),
        },
    )
    clock = FixedClock(NOW)

    async def no_sleep(_: float) -> None:
        return None

    def ctx(transport: httpx.AsyncBaseTransport) -> ConnectorContext:
        return ConnectorContext(
            creds,
            InMemoryQuota(),
            clock,
            settings=ConnectorSettings(hash_artwork=False),
            transport=transport,
            sleep=no_sleep,
        )

    repos = in_memory_repositories()
    runner = SyncRunner(
        Deps(
            connectors={
                SP: SPOTIFY_PLUGIN.create(ctx(spotify.transport)),
                TD: TIDAL_PLUGIN.create(ctx(tidal.transport)),
            },
            repos=repos,
            clock=clock,
            events=RecordingEvents(),
            new_id=SequentialIds("id"),
            breaker=CircuitBreaker(repos.breakers, clock, EngineConfig().breaker),
        )
    )
    g = group(
        source=("spotify", source),
        destinations=[("tidal", None, SyncMode.FULL)],
    )
    repos.syncs.save(g)

    first = await runner.run(g.sync_id, trigger=Trigger.MANUAL)
    assert first.status is RunStatus.SUCCEEDED, first
    saved = repos.syncs.get(g.sync_id)
    assert saved is not None
    target = saved.destinations[0].playlist_id
    assert target is not None
    assert tidal.track_ids(target) == [ta, tb, tc]
    assert tidal.playlists[target].access == "UNLISTED"

    spotify.playlists[source].uris.remove(b)
    held = await runner.run(g.sync_id, trigger=Trigger.MANUAL)
    if held.status is RunStatus.AWAITING_APPROVAL:
        (brake,) = [
            i for i in repos.attention.list_open() if i.kind is AttentionKind.BRAKE
        ]
        AttentionService(repos, clock).approve(brake.item_id)
        held = await runner.run(g.sync_id, trigger=Trigger.MANUAL)
    assert held.status is RunStatus.SUCCEEDED, held
    assert tidal.track_ids(target) == [ta, tc]


# -- Spotify → Qobuz -----------------------------------------------------------


async def test_spotify_to_qobuz_full_sync_creates_removes_and_skips_reorder() -> None:
    sp_catalog, _ = catalogs(3)
    qb_catalog = tuple(
        track(
            "qobuz",
            str(64_000_000 + i),
            title=t.title,
            artists=t.artists,
            isrc=t.isrc,
        )
        for i, t in enumerate(sp_catalog)
    )
    a, b, c = (t.track_id for t in sp_catalog)
    qa, _, qc = (t.track_id for t in qb_catalog)
    spotify, qobuz = SpotifySim(catalog=sp_catalog), QobuzSim(catalog=qb_catalog)
    source = spotify.add_playlist("Mix", [a, b, c])
    creds = InMemoryCredentials()
    creds.save(QB, qobuz.credentials())
    creds.save(
        SP,
        {
            "client_id": "a" * 32,
            "access_token": spotify.access_token,
            "refresh_token": "ref",
            "expires_at": (NOW + timedelta(hours=1)).isoformat(),
        },
    )
    clock = FixedClock(NOW)

    async def no_sleep(_: float) -> None:
        return None

    def ctx(transport: httpx.AsyncBaseTransport) -> ConnectorContext:
        return ConnectorContext(
            creds,
            InMemoryQuota(),
            clock,
            settings=ConnectorSettings(hash_artwork=False),
            transport=transport,
            sleep=no_sleep,
        )

    repos = in_memory_repositories()
    runner = SyncRunner(
        Deps(
            connectors={
                SP: SPOTIFY_PLUGIN.create(ctx(spotify.transport)),
                QB: QOBUZ_PLUGIN.create(ctx(qobuz.transport)),
            },
            repos=repos,
            clock=clock,
            events=RecordingEvents(),
            new_id=SequentialIds("id"),
            breaker=CircuitBreaker(repos.breakers, clock, EngineConfig().breaker),
        )
    )
    g = group(
        source=("spotify", source),
        destinations=[("qobuz", None, SyncMode.FULL)],
    )
    repos.syncs.save(g)

    first = await runner.run(g.sync_id, trigger=Trigger.MANUAL)
    assert first.status is RunStatus.SUCCEEDED, first
    saved = repos.syncs.get(g.sync_id)
    assert saved is not None
    target = saved.destinations[0].playlist_id
    assert target is not None
    assert qobuz.track_ids(target) == [t.track_id for t in qb_catalog]
    assert qobuz.playlists[target].is_public is False

    spotify.playlists[source].uris[:] = [c, a]  # b removed, the rest swapped
    held = await runner.run(g.sync_id, trigger=Trigger.MANUAL)
    if held.status is RunStatus.AWAITING_APPROVAL:
        (brake,) = [
            i for i in repos.attention.list_open() if i.kind is AttentionKind.BRAKE
        ]
        AttentionService(repos, clock).approve(brake.item_id)
        held = await runner.run(g.sync_id, trigger=Trigger.MANUAL)
    assert held.status is RunStatus.SUCCEEDED, held
    # Qobuz can't reorder: the removal lands, the order stays as it was.
    assert qobuz.track_ids(target) == [qa, qc]


# -- Spotify → YouTube Music ---------------------------------------------------


async def test_spotify_to_youtube_music_matches_by_isrc_search() -> None:
    """YouTube Music returns no ISRCs. Each song here differs from its Spotify
    original just enough (a typo, one artist fewer) that title and artist
    scoring alone would ask the user; the ISRC search hit settles it."""
    sp_catalog = tuple(
        track(
            "spotify",
            f"spotify:track:{i:022d}",
            title=f"Song Number {i}",
            artists=(f"Artist {i}", f"Guest {i}"),
            isrc=f"USYTM{i:07d}",
        )
        for i in range(3)
    )
    yt_catalog = tuple(
        track(
            "youtube_music",
            f"ytvid{i:06d}",
            title=f"Song Numbr {i}",
            artists=(f"Artist {i}",),
        )
        for i in range(3)
    )
    spotify = SpotifySim(catalog=sp_catalog)
    youtube = YouTubeMusicSim(
        catalog=yt_catalog,
        isrcs={
            s.isrc or "": y.track_id
            for s, y in zip(sp_catalog, yt_catalog, strict=True)
        },
    )
    source = spotify.add_playlist("Mix", [t.track_id for t in sp_catalog])
    creds = InMemoryCredentials()
    creds.save(YT, {"cookie": youtube.cookie})
    creds.save(
        SP,
        {
            "client_id": "a" * 32,
            "access_token": spotify.access_token,
            "refresh_token": "ref",
            "expires_at": (NOW + timedelta(hours=1)).isoformat(),
        },
    )
    clock = FixedClock(NOW)

    async def no_sleep(_: float) -> None:
        return None

    def ctx(transport: httpx.AsyncBaseTransport) -> ConnectorContext:
        return ConnectorContext(
            creds,
            InMemoryQuota(),
            clock,
            settings=ConnectorSettings(hash_artwork=False),
            transport=transport,
            sleep=no_sleep,
        )

    repos = in_memory_repositories()
    runner = SyncRunner(
        Deps(
            connectors={
                SP: SPOTIFY_PLUGIN.create(ctx(spotify.transport)),
                YT: YouTubeMusicPlugin(YTAuthMode.HEADERS).create(
                    ctx(youtube.transport)
                ),
            },
            repos=repos,
            clock=clock,
            events=RecordingEvents(),
            new_id=SequentialIds("id"),
            breaker=CircuitBreaker(repos.breakers, clock, EngineConfig().breaker),
        )
    )
    g = group(
        source=("spotify", source),
        destinations=[("youtube_music", None, SyncMode.FULL)],
    )
    repos.syncs.save(g)
    run = await runner.run(g.sync_id, trigger=Trigger.MANUAL)
    assert run.status is RunStatus.SUCCEEDED, run
    assert repos.attention.list_open() == []
    saved = repos.syncs.get(g.sync_id)
    assert saved is not None
    target = saved.destinations[0].playlist_id
    assert target is not None
    assert youtube.track_ids(target) == [t.track_id for t in yt_catalog]
