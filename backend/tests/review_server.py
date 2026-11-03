"""A demo world for recording the frontend's UI review build.

`python -m tests.review_server` serves the real app with fake Spotify and Tidal
connectors holding believable playlists, plus the real plugins for every other
service (shown unconnected, with their real setup guides). Never installed.
"""

import argparse
import hashlib
import signal
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import quote

import uvicorn
from fastapi import FastAPI

from linersync.adapters.connectors.kit.discovery import (
    PluginRegistry,
    discover_plugins,
)
from linersync.adapters.connectors.kit.plugin import Category, ConnectorPlugin
from linersync.adapters.fake.connector import FakeConnector
from linersync.adapters.fake.plugin import FakePlugin
from linersync.adapters.memory.backups import StaticCovers
from linersync.domain.capabilities import Capability
from linersync.domain.tracks import ServiceId, Track
from linersync.server.app import create_app
from linersync.server.cli import GRACEFUL_SHUTDOWN_SECONDS
from linersync.server.config import ServerConfig
from tests.factories import snapshot

REVIEW_PASSWORD = "review-password"

ALL = frozenset(Capability)

# (id, title, artist, album, seconds, isrc)
SONGS = [
    ("s01", "Midnight City", "M83", "Hurry Up, We're Dreaming", 243, "FR6V81100011"),
    ("s02", "Dreams", "Fleetwood Mac", "Rumours", 257, "USWB10400036"),
    ("s03", "Electric Feel", "MGMT", "Oracular Spectacular", 229, "USSM10706137"),
    ("s04", "Get Lucky", "Daft Punk", "Random Access Memories", 369, "USQX91300108"),
    ("s05", "Mr. Brightside", "The Killers", "Hot Fuss", 222, "USIR20400274"),
    ("s06", "Take On Me", "a-ha", "Hunting High and Low", 225, "GBAYE8500203"),
    ("s07", "Africa", "Toto", "Toto IV", 295, "USSM18200011"),
    (
        "s08",
        "Little Talks",
        "Of Monsters and Men",
        "My Head Is an Animal",
        266,
        "USUM71200171",
    ),
    (
        "s09",
        "Dog Days Are Over",
        "Florence + The Machine",
        "Lungs",
        252,
        "GBUM70900286",
    ),
    ("s10", "Holocene", "Bon Iver", "Bon Iver, Bon Iver", 336, "US38W1131004"),
    ("s11", "Retrograde", "James Blake", "Overgrown", 223, "GBUM71300290"),
    ("s12", "Intro", "The xx", "xx", 127, "GBBKS0900001"),
    ("s13", "Nightcall", "Kavinsky", "OutRun", 258, "FR0W61000040"),
    ("s14", "Breathe", "Télépopmusik", "Genetic World", 279, "FRZ110100041"),
    ("s15", "Teardrop", "Massive Attack", "Mezzanine", 330, "GBAAA9800145"),
    ("s16", "Reckoner", "Radiohead", "In Rainbows", 290, "GBSTK0700049"),
    ("s17", "Nude", "Radiohead", "In Rainbows", 255, None),
    ("s18", "Loading Screen (Bootleg)", "Unknown DJ", "Untitled", 188, None),
    ("s19", "Live at the Lido (Intro)", "Small Venue Band", "Live Tapes", 61, None),
    ("s20", "Clair de Lune", "Claude Debussy", "Suite bergamasque", 300, None),
]

# Missing from Tidal: these become "needs attention" after a sync. Tidal has a
# plain "Live at the Lido" though, which only the looser similar-song search finds.
NOT_ON_TIDAL = {"s18", "s19"}
LIDO = ("s19x", "Live at the Lido", "Small Venue Band", "Live Tapes", 245, None)


def _cover(album: str) -> str:
    """An album cover drawn from the album's name: works offline, in the review
    build too."""
    hue = int(hashlib.sha256(album.encode()).hexdigest()[:4], 16) % 360
    initials = "".join(w[0] for w in album.split()[:2] if w[0].isalnum()).upper()
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'>"
        f"<defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'>"
        f"<stop offset='0' stop-color='hsl({hue},55%,58%)'/>"
        f"<stop offset='1' stop-color='hsl({(hue + 40) % 360},60%,32%)'/>"
        "</linearGradient></defs><rect width='64' height='64' fill='url(#g)'/>"
        "<text x='32' y='40' font-family='sans-serif' font-size='20' "
        f"font-weight='700' fill='white' text-anchor='middle'>{initials}</text></svg>"
    )
    return "data:image/svg+xml," + quote(svg)


Song = tuple[str, str, str, str, int, str | None]


def _track(service: str, song: Song, track_id: str | None = None) -> Track:
    sid, title, artist, album, secs, isrc = song
    return Track(
        service=ServiceId(service),
        track_id=track_id or sid,
        title=title,
        artists=(artist,),
        album=album,
        duration_ms=secs * 1000,
        isrc=isrc,
        artwork_url=_cover(album),
    )


def _playlist(
    connector: FakeConnector, pid: str, name: str, ids: Sequence[str]
) -> None:
    tracks = {s[0]: _track(str(connector.service), s) for s in SONGS}
    connector.add_playlist(
        snapshot(str(connector.service), pid, list(ids), name=name, tracks=tracks)
    )


class StreamingFakePlugin(FakePlugin):
    category: Category = "streaming"


def build_app(data_dir: Path, public_url: str) -> FastAPI:
    spotify = FakeConnector(ServiceId("spotify"), "Spotify", ALL)
    _playlist(
        spotify,
        "sp-road",
        "Road Trip",
        [f"s{i:02}" for i in range(1, 10)] + ["s18", "s19"],
    )
    _playlist(
        spotify, "sp-focus", "Deep Focus", ["s10", "s11", "s12", "s14", "s15", "s20"]
    )
    _playlist(spotify, "sp-night", "Night Drive", ["s01", "s13", "s11", "s16", "s17"])
    _playlist(spotify, "sp-80s", "80s Classics", ["s06", "s07", "s02"])

    # Tidal has every song except NOT_ON_TIDAL, and two versions of "Nude"
    # so the matcher has to ask.
    tidal_catalog = [
        _track("tidal", s, "T" + s[0])
        for s in SONGS
        if s[0] not in NOT_ON_TIDAL | {"s17"}
    ]
    nude = next(s for s in SONGS if s[0] == "s17")
    tidal_catalog += [
        _track("tidal", LIDO, "Ts19x"),
        _track("tidal", nude, "Ts17a"),
        _track("tidal", (*nude[:3], "In Rainbows (Disk 2)", 262, None), "Ts17b"),
    ]
    tidal = FakeConnector(ServiceId("tidal"), "Tidal", ALL, catalog=tidal_catalog)
    tidal_songs = {s[0]: _track("tidal", s, "T" + s[0]) for s in SONGS}
    tidal.add_playlist(
        snapshot("tidal", "td-gym", ["Ts05", "Ts04"], name="Gym", tracks=tidal_songs)
    )

    plugins: dict[ServiceId, ConnectorPlugin] = {
        p.service: p
        for p in discover_plugins().plugins.values()
        if str(p.service) not in {"spotify", "tidal"}
    }
    for c in (spotify, tidal):
        plugins[c.service] = StreamingFakePlugin(c)

    return create_app(
        ServerConfig(
            admin_password=REVIEW_PASSWORD, data_dir=data_dir, public_url=public_url
        ),
        registry=PluginRegistry(plugins, ()),
        start_worker=True,
        covers=StaticCovers(),
    )


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="LinerSync UI review demo server")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--public-url", default="http://127.0.0.1:5174")
    args = parser.parse_args(argv)
    signal.signal(signal.SIGTERM, lambda _signum, _frame: sys.exit(0))
    with tempfile.TemporaryDirectory(prefix="linersync-review-") as data:
        app = build_app(Path(data), args.public_url)
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=args.port,
            timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS,
        )


if __name__ == "__main__":
    main()
