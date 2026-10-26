import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from linersync.application.runner import Deps, SyncRunner
from linersync.domain.capabilities import Capability
from linersync.domain.playlists import PlaylistMeta
from linersync.domain.runs import RunKind, SyncRun, Trigger
from linersync.domain.sync import Direction, MemberRole, SyncGroup, SyncMember, SyncMode
from linersync.domain.tracks import ServiceId


@dataclass(frozen=True, slots=True)
class BulkItem:
    source_service: ServiceId
    playlist_id: str
    destination_services: tuple[ServiceId, ...]


@dataclass(frozen=True, slots=True)
class BulkEstimate:
    playlists: int
    songs: int
    api_calls: Mapping[ServiceId, int]
    add_only_destinations: tuple[ServiceId, ...]
    unreadable: tuple[PlaylistMeta, ...]


class TransferService:
    def __init__(self, deps: Deps, runner: SyncRunner) -> None:
        self._deps = deps
        self._runner = runner

    async def transfer(
        self,
        source_service: ServiceId,
        playlist_id: str,
        destination: ServiceId,
        *,
        dry_run: bool = False,
    ) -> SyncRun:
        """Copy one playlist to a new playlist on `destination`; not a saved sync."""
        sync_id = f"transfer-{self._deps.new_id()}"
        group = SyncGroup(
            sync_id=sync_id,
            name=sync_id,
            direction=Direction.ONE_WAY,
            members=(
                SyncMember("src", source_service, playlist_id, MemberRole.SOURCE),
                SyncMember(
                    "dst0", destination, None, MemberRole.DESTINATION, SyncMode.FULL
                ),
            ),
        )
        run, _ = await self._runner.run_group(
            group,
            kind=RunKind.TRANSFER,
            trigger=Trigger.MANUAL,
            dry_run=dry_run,
            persist=False,
        )
        return run

    async def estimate_bulk(self, items: Sequence[BulkItem]) -> BulkEstimate:
        """Estimate from listing metadata only; no playlist is read."""
        listings: dict[ServiceId, dict[str, PlaylistMeta]] = {}
        for item in items:
            svc = item.source_service
            if svc not in listings:
                metas = await self._deps.connectors[svc].list_playlists()
                listings[svc] = {m.playlist_id: m for m in metas}
        calls: dict[ServiceId, int] = {}
        unreadable: list[PlaylistMeta] = []
        add_only: list[ServiceId] = []
        playlists = songs = 0
        for item in items:
            meta = listings[item.source_service].get(item.playlist_id)
            if meta is None:
                raise ValueError(
                    f"playlist {item.playlist_id!r} not found on {item.source_service}"
                )
            if not meta.readable:
                unreadable.append(meta)
                continue
            n = meta.track_count or 0
            playlists += 1
            songs += n
            src = self._deps.connectors[item.source_service].limits
            calls[item.source_service] = calls.get(item.source_service, 0) + math.ceil(
                n / src.page_size
            )
            for dest in item.destination_services:
                conn = self._deps.connectors[dest]
                lim = conn.limits
                calls[dest] = calls.get(dest, 0) + (
                    n * lim.search_cost + math.ceil(n / lim.add_batch_size) + 1
                )
                if Capability.REMOVE not in conn.capabilities and dest not in add_only:
                    add_only.append(dest)
        return BulkEstimate(
            playlists=playlists,
            songs=songs,
            api_calls={s: c for s, c in calls.items() if c > 0},
            add_only_destinations=tuple(add_only),
            unreadable=tuple(unreadable),
        )
