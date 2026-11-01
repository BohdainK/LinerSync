from dataclasses import dataclass
from typing import Protocol

from linersync.domain.capabilities import Capability
from linersync.domain.operations import PlaylistOp
from linersync.domain.playlists import PlaylistDraft, PlaylistMeta, PlaylistSnapshot
from linersync.domain.tracks import ServiceId, Track, TrackQuery


@dataclass(frozen=True, slots=True)
class ConnectorLimits:
    page_size: int
    add_batch_size: int
    search_cost: int = 1


class Connector(Protocol):
    service: ServiceId
    display_name: str
    capabilities: frozenset[Capability]
    limits: ConnectorLimits

    async def list_playlists(self) -> list[PlaylistMeta]: ...

    async def read_playlist(self, playlist_id: str) -> PlaylistSnapshot: ...

    async def search(self, query: TrackQuery) -> list[Track]: ...

    async def create_playlist(self, draft: PlaylistDraft) -> PlaylistMeta: ...

    async def apply(
        self,
        playlist_id: str,
        op: PlaylistOp,
    ) -> None: ...
