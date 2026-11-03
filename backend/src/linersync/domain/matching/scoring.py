import difflib
import re
from collections.abc import Sequence

from linersync.domain.matching.normalize import (
    IGNORED_VERSIONS,
    normalize_artists,
    normalize_text,
    parse_title,
)
from linersync.domain.matching.types import (
    MatchConfig,
    Matched,
    MatchVia,
    NeedsAttention,
    ScoredCandidate,
)
from linersync.domain.tracks import Track

_ISRC_FLOOR = 0.97
# Just under a real ISRC match: the hit is only a candidate until title,
# artists and duration agree with it.
_ISRC_SEARCH_FLOOR = 0.95
_ISRC_SEARCH_TITLE = 0.9
_ISRC_SEARCH_ARTISTS = 0.5
_ISRC_SEARCH_REASON = "found by ISRC search"
_ISRC_DIFFERS = 0.05
_SAME_ALBUM = 0.9
_SAME_ALBUM_REASON = "same album"
# Release-type suffixes services add to an album name ("Name - EP").
_ALBUM_SUFFIX = re.compile(r"\s+(ep|single)$")


def _artist_similarity(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.5
    return len(a & b) / len(a | b)


def _duration_score(delta_s: float | None, cfg: MatchConfig) -> float:
    if delta_s is None:
        return 0.5
    if delta_s <= cfg.duration_full_s:
        return 1.0
    if delta_s >= cfg.duration_zero_s:
        return 0.0
    return (cfg.duration_zero_s - delta_s) / (cfg.duration_zero_s - cfg.duration_full_s)


def _artwork_bits(artwork_hash: str | None) -> int | None:
    """The hash as an int; an unparsable hash counts as missing."""
    if artwork_hash is None:
        return None
    try:
        return int(artwork_hash, 16)
    except ValueError:
        return None


def _same_isrc(source: Track, candidate: Track) -> bool:
    return (
        source.isrc is not None
        and candidate.isrc is not None
        and source.isrc.casefold() == candidate.isrc.casefold()
    )


def _album_similarity(source: Track, candidate: Track) -> float:
    """Breaks ties between copies of one recording (same ISRC) on different
    albums, so a compilation doesn't win over the source's own album."""
    if not source.album or not candidate.album:
        return 0.0
    a, b = _album_key(source.album), _album_key(candidate.album)
    return difflib.SequenceMatcher(None, a, b).ratio()


def _album_key(album: str) -> str:
    return _ALBUM_SUFFIX.sub("", normalize_text(album))


def score_candidate(
    source: Track, candidate: Track, cfg: MatchConfig
) -> ScoredCandidate:
    ps = parse_title(source.title)
    pc = parse_title(candidate.title)
    title_sim = difflib.SequenceMatcher(None, ps.base, pc.base).ratio()
    artist_sim = _artist_similarity(
        normalize_artists(source.artists, ps.featured),
        normalize_artists(candidate.artists, pc.featured),
    )
    delta_s: float | None = None
    if source.duration_ms is not None and candidate.duration_ms is not None:
        delta_s = abs(source.duration_ms - candidate.duration_ms) / 1000
    conf = 0.5 * title_sim + 0.3 * artist_sim + 0.2 * _duration_score(delta_s, cfg)
    reasons: list[str] = []

    if source.isrc is None:
        reasons.append("no ISRC")
    elif _same_isrc(source, candidate):
        pass  # applied below, after the other adjustments
    elif candidate.isrc is not None:
        conf -= _ISRC_DIFFERS
        reasons.append("ISRC differs")

    version = (ps.versions ^ pc.versions) - IGNORED_VERSIONS
    if version:
        conf -= 0.3
        reasons.append(f"version differs ({', '.join(sorted(version))})")

    if delta_s is not None and delta_s > cfg.duration_full_s:
        reasons.append(f"duration differs by {round(delta_s)}s")

    src_art = _artwork_bits(source.artwork_hash)
    cand_art = _artwork_bits(candidate.artwork_hash)
    if src_art is not None and cand_art is not None:
        if (src_art ^ cand_art).bit_count() <= cfg.artwork_max_distance:
            conf += 0.05
            reasons.append("same artwork")
        else:
            conf -= 0.02
            reasons.append("different artwork")

    agrees = (
        title_sim >= _ISRC_SEARCH_TITLE
        and artist_sim >= _ISRC_SEARCH_ARTISTS
        and delta_s is not None
        and delta_s <= cfg.duration_full_s
        and not version
    )
    if candidate.found_by_isrc and source.isrc is not None and agrees:
        conf = max(conf, _ISRC_SEARCH_FLOOR)
        reasons.append(_ISRC_SEARCH_REASON)

    # A label can give one recording a new ISRC per release: a copy on the
    # source's own album that agrees ranks with an ISRC match (the ISRC
    # penalty is lifted), and the album tie-break then prefers it over the
    # ISRC's copies on compilations.
    if (
        not _same_isrc(source, candidate)
        and _album_similarity(source, candidate) >= _SAME_ALBUM
        and agrees
    ):
        if "ISRC differs" in reasons:
            conf += _ISRC_DIFFERS
        conf = max(conf, _ISRC_FLOOR)
        reasons.append(_SAME_ALBUM_REASON)

    if _same_isrc(source, candidate):
        conf = max(conf, _ISRC_FLOOR)
        reasons.insert(0, "ISRC matches")

    return ScoredCandidate(candidate, min(1.0, max(0.0, conf)), tuple(reasons))


def _same_recording(source: Track, other: ScoredCandidate) -> bool:
    """`other` is a copy of the source's recording on another release."""
    return _same_isrc(source, other.track) or _SAME_ALBUM_REASON in other.reasons


def decide_match(
    source: Track, candidates: Sequence[Track], cfg: MatchConfig
) -> Matched | NeedsAttention:
    if not candidates:
        return NeedsAttention(("no results on this service",), ())
    scored = sorted(
        (score_candidate(source, c, cfg) for c in candidates),
        key=lambda s: (s.confidence, _album_similarity(source, s.track)),
        reverse=True,
    )
    top = scored[0]
    if top.confidence >= cfg.accept_threshold and (
        len(scored) == 1
        or top.confidence - scored[1].confidence >= cfg.min_margin
        or _same_isrc(source, top.track)
        # One recording on several releases: the album tie-break above chose.
        or (
            _ISRC_SEARCH_REASON in top.reasons
            and _ISRC_SEARCH_REASON in scored[1].reasons
        )
        or (_SAME_ALBUM_REASON in top.reasons and _same_recording(source, scored[1]))
    ):
        return Matched(
            target_track_id=top.track.track_id,
            confidence=top.confidence,
            reasons=top.reasons,
            via=MatchVia.SCORED,
        )
    return NeedsAttention(top.reasons, tuple(scored[: cfg.max_candidates]))
