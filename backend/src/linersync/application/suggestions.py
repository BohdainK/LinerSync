"""Looser searches for a song that didn't match, so the person can pick one."""

import re
from collections.abc import Sequence

from linersync.domain.matching.scoring import score_candidate
from linersync.domain.matching.types import MatchConfig, ScoredCandidate
from linersync.domain.tracks import Track, TrackKey, TrackQuery
from linersync.ports.connector import Connector

# Below this a candidate is rarely the same song: a shared title with another
# artist, or a shared artist with a near title, scores around here.
SUGGEST_FLOOR = 0.5
MAX_SUGGESTIONS = 8

_BRACKETS = re.compile(r"\s*[(\[][^)\]]*[)\]]")
_DASH_SUFFIX = re.compile(r"\s+-\s+.*$")


def bare_title(title: str) -> str:
    """The title without bracketed or dashed extras ("(Remastered 2011)",
    "- Live"), which often keep a strict search from finding anything."""
    stripped = _DASH_SUFFIX.sub("", _BRACKETS.sub("", title)).strip()
    return stripped or title


def _queries(source: Track) -> list[TrackQuery]:
    title = bare_title(source.title)
    queries = [TrackQuery(title, source.artists[:1])]
    if source.artists:
        queries.append(TrackQuery(title, ()))
    return queries


async def find_similar(
    source: Track,
    target: Connector,
    cfg: MatchConfig,
    known: Sequence[ScoredCandidate] = (),
) -> tuple[ScoredCandidate, ...]:
    """`known` plus songs found by looser searches on `target` scoring at least
    SUGGEST_FLOOR, best first. Raises ConnectorError when a search fails."""
    found: dict[TrackKey, Track] = {}
    for query in _queries(source):
        for t in await target.search(query):
            found.setdefault(t.key, t)
    scored = {c.track.key: c for c in known}
    for key, t in found.items():
        if key not in scored:
            c = score_candidate(source, t, cfg)
            if c.confidence >= SUGGEST_FLOOR:
                scored[key] = c
    ranked = sorted(scored.values(), key=lambda c: c.confidence, reverse=True)
    keep = {c.track.key for c in known}
    out = [c for c in ranked if c.track.key in keep]
    out += [c for c in ranked if c.track.key not in keep][:MAX_SUGGESTIONS]
    return tuple(sorted(out, key=lambda c: c.confidence, reverse=True))


async def search_text(
    source: Track, target: Connector, cfg: MatchConfig, text: str
) -> tuple[ScoredCandidate, ...]:
    """Songs on `target` found by searching `text`, each scored against
    `source`, best first. No floor: the person is looking on purpose. Raises
    ConnectorError when the search fails."""
    scored = {
        t.key: score_candidate(source, t, cfg)
        for t in await target.search(TrackQuery(text, ()))
    }
    ranked = sorted(scored.values(), key=lambda c: c.confidence, reverse=True)
    return tuple(ranked[:MAX_SUGGESTIONS])
