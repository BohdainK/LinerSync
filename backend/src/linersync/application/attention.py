from dataclasses import replace
from typing import Any

from linersync.domain.attention import AttentionItem, AttentionKind
from linersync.domain.matching.types import RememberedMatch
from linersync.domain.sync import SyncStatus
from linersync.ports.repositories import AttentionRepository, Repositories
from linersync.ports.runtime import Clock


def open_items(
    repo: AttentionRepository, kind: AttentionKind, **match: Any
) -> list[AttentionItem]:
    """The open items of `kind` whose fields equal `match`."""
    return [
        item
        for item in repo.list_open()
        if item.kind is kind
        and all(getattr(item, name) == value for name, value in match.items())
    ]


class AttentionService:
    """Resolves "Needs attention" items. A wrong-kind, already-resolved or
    unknown item raises ValueError."""

    def __init__(self, repos: Repositories, clock: Clock) -> None:
        self._repos = repos
        self._clock = clock

    def pick_candidate(self, item_id: str, target_track_id: str) -> None:
        item = self._open(item_id, AttentionKind.UNMATCHED)
        self._remember(item, target_track_id, f"picked {target_track_id}")

    def mark_not_on_service(self, item_id: str) -> None:
        item = self._open(item_id, AttentionKind.UNMATCHED)
        self._remember(item, None, "not on service")

    def approve(self, item_id: str) -> None:
        item = self._open(item_id, AttentionKind.BRAKE)
        if item.sync_id is None or item.member_id is None or item.fingerprint is None:
            raise ValueError(f"item {item_id} has no sync member or fingerprint")
        self._repos.approvals.approve(item.sync_id, item.member_id, item.fingerprint)
        self._resolve(item, "approved")

    def resume_sync(self, item_id: str) -> None:
        item = self._open(item_id, AttentionKind.SOURCE_MISSING)
        group = (
            self._repos.syncs.get(item.sync_id) if item.sync_id is not None else None
        )
        if group is None:
            raise ValueError(f"sync {item.sync_id} does not exist")
        self._repos.syncs.save(
            replace(group, status=SyncStatus.ACTIVE, pause_reason=None)
        )
        self._resolve(item, "resumed")

    def dismiss(self, item_id: str) -> None:
        self._resolve(self._open(item_id, None), "dismissed")

    def _open(self, item_id: str, kind: AttentionKind | None) -> AttentionItem:
        item = self._repos.attention.get(item_id)
        if item is None:
            raise ValueError(f"unknown attention item {item_id}")
        if item.resolved_at is not None:
            raise ValueError(f"attention item {item_id} is already resolved")
        if kind is not None and item.kind is not kind:
            raise ValueError(f"attention item {item_id} is {item.kind}, not {kind}")
        return item

    def _remember(
        self, item: AttentionItem, target_track_id: str | None, resolution: str
    ) -> None:
        if item.source is None or item.target_service is None:
            raise ValueError(f"item {item.item_id} has no source song or target")
        key, service = item.source.key, item.target_service
        self._repos.matches.remember(RememberedMatch(key, service, target_track_id))
        unmatched = open_items(
            self._repos.attention, AttentionKind.UNMATCHED, target_service=service
        )
        for other in unmatched:
            if other.source is not None and other.source.key == key:
                self._resolve(other, resolution)

    def _resolve(self, item: AttentionItem, resolution: str) -> None:
        self._repos.attention.resolve(item.item_id, resolution, self._clock.now())
