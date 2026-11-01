"""Building blocks shared by the connector packages."""

from linersync.domain.operations import PlaylistOp

__all__ = ["SEARCH_LIMIT", "PlaylistOp"]

SEARCH_LIMIT = 10  # candidates a search returns at most
