from dataclasses import dataclass, field, replace

import pytest

from linersync.adapters.fake.connector import FakeConnector
from linersync.adapters.memory.repositories import in_memory_repositories
from linersync.adapters.memory.runtime import FixedClock, RecordingEvents, SequentialIds
from linersync.application.breaker import CircuitBreaker
from linersync.application.config import EngineConfig
from linersync.application.runner import Deps, SyncRunner
from linersync.domain.capabilities import Capability
from linersync.domain.sync import SyncGroup, SyncMode
from linersync.domain.tracks import ServiceId
from linersync.ports.repositories import Repositories
from tests.factories import NOW, group, snapshot, track

SPOTIFY = ServiceId("spotify")
APPLE = ServiceId("apple_music")


@dataclass
class World:
    """Spotify P1 (a, b, c) synced one way into an empty Apple Music A1.

    Apple's catalog holds A, B and C, titled a, b and c, so search matches
    a→A, b→B and c→C.
    """

    spotify: FakeConnector
    apple: FakeConnector
    repos: Repositories = field(default_factory=in_memory_repositories)
    clock: FixedClock = field(default_factory=lambda: FixedClock(NOW))
    events: RecordingEvents = field(default_factory=RecordingEvents)
    ids: SequentialIds = field(default_factory=lambda: SequentialIds("id"))
    group: SyncGroup = field(default_factory=group)

    @property
    def deps(self) -> Deps:
        return Deps(
            connectors={SPOTIFY: self.spotify, APPLE: self.apple},
            repos=self.repos,
            clock=self.clock,
            events=self.events,
            new_id=self.ids,
            breaker=CircuitBreaker(
                self.repos.breakers, self.clock, EngineConfig().breaker
            ),
        )

    def runner(self) -> SyncRunner:
        return SyncRunner(self.deps)

    def spotify_to_spotify(
        self,
        target: tuple[str, ...] = (),
        *,
        playlist_id: str = "P2",
        mode: SyncMode = SyncMode.FULL,
        sync_id: str = "s2",
        mirror: bool = False,
    ) -> SyncGroup:
        """Seed Spotify `playlist_id` with `target` and save a P1→it group.
        `mirror` makes the first full run mirror the source (`mirror_once`):
        otherwise the seeded songs count as the user's and are kept."""
        self.spotify.add_playlist(snapshot("spotify", playlist_id, target))
        g = group(destinations=[("spotify", playlist_id, mode)], sync_id=sync_id)
        if mirror:
            g = g.with_member(replace(g.destinations[0], mirror_once=True))
        self.repos.syncs.save(g)
        return g


def _make_world(*, save_group: bool) -> World:
    spotify = FakeConnector(SPOTIFY, "Spotify", frozenset(Capability))
    apple = FakeConnector(
        APPLE,
        "Apple Music",
        frozenset({Capability.READ_ORDER, Capability.ADD, Capability.CREATE_PLAYLIST}),
        catalog=[track("apple_music", t.upper(), title=t) for t in ("a", "b", "c")],
    )
    spotify.add_playlist(snapshot("spotify", "P1", ["a", "b", "c"]))
    apple.add_playlist(snapshot("apple_music", "A1", []))
    w = World(spotify=spotify, apple=apple)
    if save_group:
        w.repos.syncs.save(w.group)
    return w


@pytest.fixture
def world() -> World:
    return _make_world(save_group=True)


@pytest.fixture
def unsaved_world() -> World:
    """Like `world`, but the group is not saved in the repositories."""
    return _make_world(save_group=False)
