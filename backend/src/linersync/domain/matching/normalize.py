import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

VERSION_TAGS: Final = frozenset(
    {
        "live",
        "remix",
        "acoustic",
        "instrumental",
        "radio edit",
        "demo",
        "remaster",
        "extended",
        "mono",
        "stereo",
        "edit",
    }
)
IGNORED_VERSIONS: Final = frozenset({"remaster", "mono", "stereo"})

_TAG_SUFFIX: Final = {"remaster": "(?:ed|s)?", "remix": "(?:ed|es)?"}
# Longest first, so "radio edit" is consumed before "edit" can match.
_TAG_PATTERNS: Final = tuple(
    (tag, re.compile(rf"\b{re.escape(tag)}{_TAG_SUFFIX.get(tag, '')}\b"))
    for tag in sorted(VERSION_TAGS, key=len, reverse=True)
)
_BRACKET: Final = re.compile(r"\(([^()]*)\)|\[([^\[\]]*)\]")
_FEAT: Final = re.compile(r"^\s*(?:feat\.?|ft\.?|featuring)\s+(.+?)\s*$", re.IGNORECASE)
_ARTIST_SPLIT: Final = re.compile(
    r",\s|\s&\s|\s(?:feat\.|ft\.|featuring)\s", re.IGNORECASE
)
_DASH: Final = " - "
_LIVE_LOCATION: Final = re.compile(r"\blive (?:at|from)\b.*$")
_FILLER: Final = re.compile(r"\b(?:version|mix|\d+)\b")
_VARIOUS: Final = "various artists"


@dataclass(frozen=True, slots=True)
class ParsedTitle:
    base: str
    versions: frozenset[str]
    featured: tuple[str, ...]


def normalize_text(s: str) -> str:
    decomposed = unicodedata.normalize("NFKD", s)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    text = stripped.casefold().replace("&", " and ")
    text = re.sub(r"['’‘`]", "", text)
    text = re.sub(r"[^\w\s]|_", " ", text)
    return " ".join(text.split())


def _split_artists(value: str) -> list[str]:
    parts = (normalize_text(p) for p in _ARTIST_SPLIT.split(value))
    return [p for p in parts if p]


def _find_tags(segment: str) -> frozenset[str]:
    text = normalize_text(segment)
    found: set[str] = set()
    for tag, pattern in _TAG_PATTERNS:
        if pattern.search(text):
            found.add(tag)
            text = pattern.sub(" ", text)
    return frozenset(found)


def _classify(segment: str) -> tuple[frozenset[str], tuple[str, ...]] | None:
    """Return (versions, featured) if the segment is a marker, else None."""
    feat = _FEAT.match(segment)
    if feat:
        return frozenset(), tuple(_split_artists(feat.group(1)))
    tags = _find_tags(segment)
    if tags:
        return tags, ()
    return None


def _classify_dash(segment: str) -> tuple[frozenset[str], tuple[str, ...]] | None:
    """Like _classify, but a tag segment must contain nothing else meaningful."""
    feat = _FEAT.match(segment)
    if feat:
        return frozenset(), tuple(_split_artists(feat.group(1)))
    text = _LIVE_LOCATION.sub("live", normalize_text(segment))
    tags: set[str] = set()
    for tag, pattern in _TAG_PATTERNS:
        if pattern.search(text):
            tags.add(tag)
            text = pattern.sub(" ", text)
    if tags and not _FILLER.sub(" ", text).strip():
        return frozenset(tags), ()
    return None


def parse_title(title: str) -> ParsedTitle:
    versions: set[str] = set()
    featured: list[str] = []

    def absorb(match: re.Match[str]) -> str:
        segment = match.group(1) if match.group(1) is not None else match.group(2)
        result = _classify(segment)
        if result is None:
            return match.group(0)
        versions.update(result[0])
        featured.extend(result[1])
        return " "

    remaining = _BRACKET.sub(absorb, title)
    segments = remaining.split(_DASH)
    while len(segments) > 1:
        result = _classify_dash(segments[-1])
        if result is None:
            break
        versions.update(result[0])
        featured.extend(result[1])
        segments.pop()
    remaining = " ".join(segments)
    return ParsedTitle(
        base=normalize_text(remaining),
        versions=frozenset(versions),
        featured=tuple(featured),
    )


def normalize_artists(
    artists: Sequence[str], featured: Sequence[str] = ()
) -> frozenset[str]:
    names: set[str] = set()
    for value in (*artists, *featured):
        names.update(_split_artists(value))
    names.discard(_VARIOUS)
    return frozenset(names)
