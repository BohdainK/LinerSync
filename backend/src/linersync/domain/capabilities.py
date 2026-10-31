from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from linersync.domain.sync import SyncMode


class Capability(StrEnum):
    READ_ORDER = "read_order"
    ADD = "add"
    REMOVE = "remove"
    REORDER = "reorder"
    EDIT_METADATA = "edit_metadata"
    CREATE_PLAYLIST = "create_playlist"
    DELETE_PLAYLIST = "delete_playlist"


def limitations(
    service_name: str, caps: frozenset[Capability], mode: "SyncMode"
) -> tuple[str, ...]:
    # sync.py imports Capability from here, so SyncMode is imported lazily.
    from linersync.domain.sync import SyncMode

    name = service_name
    full = mode is SyncMode.FULL
    if Capability.ADD not in caps:
        return (f"{name} is read-only, so it can't be a destination",)
    out: list[str] = []
    if Capability.REMOVE not in caps:
        if Capability.REORDER not in caps:
            out.append(
                f"{name} can't remove or reorder songs, "
                f"so this sync runs as add only into {name}"
            )
        else:
            out.append(
                f"{name} can't remove songs, so this sync runs as add only into {name}"
            )
    elif Capability.REORDER not in caps and full:
        out.append(
            f"{name} can't reorder songs, so song order won't carry over to {name}"
        )
    if Capability.EDIT_METADATA not in caps and full:
        out.append(
            f"{name} can't edit playlist details, "
            "so name and description changes won't carry over"
        )
    if Capability.CREATE_PLAYLIST not in caps:
        out.append(
            f"{name} can't create playlists, "
            "so the destination playlist must already exist"
        )
    return tuple(out)
