"""Combining a search's ISRC lookup with its title search."""

from collections.abc import Iterable

from linersync.domain.tracks import Track


def merge_hits(isrc_hits: Iterable[Track], term_hits: Iterable[Track]) -> list[Track]:
    """ISRC hits, then term hits not already among them. Callers run the term
    search even when the ISRC lookup finds something: a label may give the
    album copy another ISRC than the source's, leaving only compilations under
    the source's ISRC. Each list keeps its own cap, so ISRC hits never crowd
    out the term hits."""
    found: list[Track] = []
    seen: set[str] = set()
    for t in (*isrc_hits, *term_hits):
        if t.track_id not in seen:
            found.append(t)
            seen.add(t.track_id)
    return found
