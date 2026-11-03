"""Swaps the person chose in a preview, saved so the real run uses them."""

from collections.abc import Iterable

from linersync.domain.matching.types import RememberedMatch
from linersync.domain.tracks import ServiceId, TrackKey
from linersync.ports.repositories import MatchRepository


def remember_picks(
    repo: MatchRepository, picks: Iterable[tuple[TrackKey, ServiceId, str]]
) -> None:
    """Remember each (source song, target service, chosen target song). A later
    pick for the same source song and service wins."""
    for source_key, target_service, target_track_id in picks:
        repo.remember(RememberedMatch(source_key, target_service, target_track_id))
