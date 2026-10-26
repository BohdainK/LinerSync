from collections.abc import Mapping

LIVE_ENV: dict[str, tuple[str, ...]] = {
    "spotify": ("SPOTIFY_CLIENT_ID", "SPOTIFY_REFRESH_TOKEN"),
    "apple_music": (
        "APPLE_TEAM_ID",
        "APPLE_KEY_ID",
        "APPLE_PRIVATE_KEY_PATH",
        "APPLE_MUSIC_USER_TOKEN",
    ),
}


def live_skip_reason(service: str, env: Mapping[str, str]) -> str | None:
    """Why a service's live tests must be skipped, or None when they may run."""
    if env.get("LINERSYNC_LIVE") != "1":
        return "set LINERSYNC_LIVE=1 to run live tests"
    missing = [name for name in LIVE_ENV[service] if not env.get(name)]
    if missing:
        return f"missing environment variables: {', '.join(missing)}"
    return None
