from dataclasses import dataclass
from typing import Protocol, runtime_checkable

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


@runtime_checkable
class DeletesPlaylists(Protocol):
    """A connector that declares DELETE_PLAYLIST implements this too."""

    async def delete_playlist(self, playlist_id: str) -> None:
        """Delete the playlist; one already missing counts as deleted."""
        ...
