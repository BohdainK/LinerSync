"""Response pieces shared by the run and needs-attention routes."""

from collections.abc import Iterable

from linersync.domain.operations import ChangeNotMade
from linersync.domain.tracks import ServiceId, Track, TrackKey
from linersync.server.api.schemas import ChangeOut, TrackOut


class TrackNames:
    """Looks songs up in the run's context; an unknown id keeps its id as title."""

    def __init__(self, tracks: Iterable[Track]) -> None:
        self._tracks: dict[TrackKey, Track] = {t.key: t for t in tracks}
        by_id: dict[str, list[Track]] = {}
        for t in self._tracks.values():
            by_id.setdefault(t.track_id, []).append(t)
        self._by_id = by_id

    def track_named(self, service: str, track_id: str) -> TrackOut:
        """Like `track`, but an id unknown on `service` takes the one context
        song with that id on another service (a copy to source reports
        destination songs it couldn't copy on the source member)."""
        if TrackKey(ServiceId(service), track_id) not in self._tracks:
            others = self._by_id.get(track_id, [])
            if len(others) == 1:
                return TrackOut.of(others[0])
        return self.track(service, track_id)

    def track(self, service: str, track_id: str) -> TrackOut:
        found = self._tracks.get(TrackKey(ServiceId(service), track_id))
        if found is None:
            return TrackOut(
                service=service,
                track_id=track_id,
                title=track_id,
                artists=[],
                album=None,
                duration_ms=None,
                isrc=None,
                url=None,
                known=False,
            )
        return TrackOut.of(found)

    def tracks(self, service: str, track_ids: Iterable[str]) -> list[TrackOut]:
        return [self.track(service, tid) for tid in track_ids]


def change_out(change: ChangeNotMade, service: str, names: TrackNames) -> ChangeOut:
    return ChangeOut(
        kind=change.kind,
        detail=change.detail,
        track=None
        if change.track_id is None
        else names.track_named(service, change.track_id),
    )
