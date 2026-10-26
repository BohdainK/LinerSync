from linersync.domain.matching.scoring import decide_match
from linersync.domain.matching.types import (
    MatchConfig,
    MatchDecision,
    Matched,
    MatchVia,
    NotOnService,
)
from linersync.domain.playlists import PlaylistSnapshot
from linersync.domain.sync import MemberState
from linersync.domain.tracks import ServiceId, Track, TrackKey, TrackQuery
from linersync.ports.connector import Connector
from linersync.ports.errors import ConnectorError
from linersync.ports.repositories import MatchRepository


class MatchingFailed(Exception):
    def __init__(self, service: ServiceId, message: str = "") -> None:
        super().__init__(f"{service}: {message}" if message else str(service))
        self.service = service


class MatchService:
    def __init__(self, matches: MatchRepository, cfg: MatchConfig) -> None:
        self._matches = matches
        self._cfg = cfg

    async def resolve(
        self,
        source: PlaylistSnapshot,
        target: Connector,
        previous: MemberState | None,
    ) -> dict[TrackKey, MatchDecision]:
        paired: dict[TrackKey, str] = {}
        if previous is not None:
            for entry in previous.entries:
                paired.setdefault(entry.source_key, entry.target_track_id)
        out: dict[TrackKey, MatchDecision] = {}
        for item in source.entries:
            t = item.track
            if t.key not in out:
                out[t.key] = await self._resolve_one(t, target, paired)
        return out

    async def _resolve_one(
        self, t: Track, target: Connector, paired: dict[TrackKey, str]
    ) -> MatchDecision:
        if t.service == target.service:
            return Matched(t.track_id, 1.0, (), MatchVia.SAME_SERVICE)
        remembered = self._matches.get(t.key, target.service)
        if remembered is not None:
            if remembered.target_track_id is None:
                return NotOnService()
            return Matched(
                remembered.target_track_id, 1.0, ("your pick",), MatchVia.REMEMBERED
            )
        prior = paired.get(t.key)
        if prior is not None:
            return Matched(prior, 1.0, (), MatchVia.PREVIOUS_SYNC)
        try:
            candidates = await target.search(TrackQuery.from_track(t))
        except ConnectorError as e:
            raise MatchingFailed(target.service, e.message or str(e)) from e
        return decide_match(t, candidates, self._cfg)
