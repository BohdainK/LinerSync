from collections.abc import Mapping

from linersync.adapters.connectors.apple_music.plugin import (
    APPLE_MUSIC_PLUGIN,
    AuthMode,
)
from linersync.adapters.connectors.youtube_music.plugin import (
    YOUTUBE_MUSIC_PLUGIN,
    YTAuthMode,
)

# With LINERSYNC_APPLE_MUSIC_AUTH=web (the default) APPLE_MUSIC_USER_TOKEN is the
# media-user-token cookie from music.apple.com; with developer it comes from
# MusicKit sign-in (python -m linersync.devtools.live_login apple-music).
_APPLE_ENV = {
    AuthMode.WEB: ("APPLE_MUSIC_USER_TOKEN",),
    AuthMode.DEVELOPER: (
        "APPLE_TEAM_ID",
        "APPLE_KEY_ID",
        "APPLE_PRIVATE_KEY_PATH",
        "APPLE_MUSIC_USER_TOKEN",
    ),
}
# LINERSYNC_YTMUSIC_AUTH=headers (default): the Cookie header of a signed-in
# music.youtube.com tab; oauth: python -m linersync.devtools.live_login
# youtube-music.
_YTMUSIC_ENV = {
    YTAuthMode.HEADERS: ("YTMUSIC_COOKIE",),
    YTAuthMode.OAUTH: (
        "YTMUSIC_CLIENT_ID",
        "YTMUSIC_CLIENT_SECRET",
        "YTMUSIC_REFRESH_TOKEN",
    ),
}
LIVE_ENV: dict[str, tuple[str, ...]] = {
    "spotify": ("SPOTIFY_CLIENT_ID", "SPOTIFY_REFRESH_TOKEN"),
    "apple_music": _APPLE_ENV[APPLE_MUSIC_PLUGIN.mode],
    "jellyfin": ("JELLYFIN_URL", "JELLYFIN_API_KEY", "JELLYFIN_USER"),
    # From python -m linersync.devtools.live_login tidal --client-id ID
    "tidal": ("TIDAL_CLIENT_ID", "TIDAL_REFRESH_TOKEN"),
    "youtube_music": _YTMUSIC_ENV[YOUTUBE_MUSIC_PLUGIN.mode],
    # The X-App-Id and X-User-Auth-Token headers of a signed-in play.qobuz.com
    "qobuz": ("QOBUZ_APP_ID", "QOBUZ_USER_AUTH_TOKEN"),
}


def live_skip_reason(service: str, env: Mapping[str, str]) -> str | None:
    """Why a service's live tests must be skipped, or None when they may run."""
    if env.get("LINERSYNC_LIVE") != "1":
        return "set LINERSYNC_LIVE=1 to run live tests"
    missing = [name for name in LIVE_ENV[service] if not env.get(name)]
    if missing:
        return f"missing environment variables: {', '.join(missing)}"
    return None
