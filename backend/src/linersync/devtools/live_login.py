"""Local sign-in helper for the opt-in live tests.

    python -m linersync.devtools.live_login spotify --client-id ID [--port 8765]
    python -m linersync.devtools.live_login tidal --client-id ID [--port 8767]
    python -m linersync.devtools.live_login youtube-music --client-id ID \
        --client-secret SECRET
    python -m linersync.devtools.live_login apple-music --team-id T --key-id K \
        --key-file PATH [--port 8766]

It serves on 127.0.0.1 only, finishes one sign-in, prints `export ...` lines and
exits. Nothing is written to disk.
"""

import argparse
import asyncio
import json
import secrets
import sys
import webbrowser
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from linersync.adapters.connectors.apple_music.token import make_developer_token
from linersync.adapters.connectors.kit.oauth import pkce_pair
from linersync.adapters.connectors.kit.plugin import ConnectorContext, OAuthSetup
from linersync.adapters.connectors.spotify.auth import authorize_url
from linersync.adapters.connectors.spotify.plugin import SPOTIFY_PLUGIN
from linersync.adapters.connectors.tidal.auth import (
    authorize_url as tidal_authorize_url,
)
from linersync.adapters.connectors.tidal.plugin import TIDAL_PLUGIN
from linersync.adapters.connectors.youtube_music.plugin import (
    YouTubeMusicPlugin,
    YTAuthMode,
)
from linersync.adapters.memory.credentials import InMemoryCredentials
from linersync.adapters.memory.quota import InMemoryQuota
from linersync.domain.tracks import ServiceId

MUSICKIT_URL = "https://js-cdn.music.apple.com/musickit/v3/musickit.js"


class _SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


def _redirect_uri(port: int) -> str:
    return f"http://127.0.0.1:{port}/callback"


def spotify_login_url(client_id: str, port: int, state: str, challenge: str) -> str:
    return authorize_url(client_id, _redirect_uri(port), state, challenge)


def tidal_login_url(client_id: str, port: int, state: str, challenge: str) -> str:
    return tidal_authorize_url(client_id, _redirect_uri(port), state, challenge)


class LoginFailed(Exception):
    """The sign-in ended without credentials; the message says why."""


@dataclass(frozen=True, slots=True)
class CallbackResult:
    status: int
    message: str
    finished: bool
    code: str | None = None
    error: str | None = None


def oauth_callback(
    query: Mapping[str, list[str]], state: str, display_name: str
) -> CallbackResult:
    """What the local server answers to a service's redirect, and whether the
    sign-in is over. An error from the service (e.g. access_denied) ends it."""
    if query.get("state") != [state]:
        return CallbackResult(400, "Sign-in failed or state mismatch", False)
    if "error" in query:
        error = query["error"][0]
        return CallbackResult(
            400, f"{display_name} sign-in failed: {error}", True, error=error
        )
    if "code" not in query:
        return CallbackResult(400, "Sign-in failed or state mismatch", False)
    return CallbackResult(
        200, "Signed in. You can close this tab.", True, code=query["code"][0]
    )


def spotify_callback(query: Mapping[str, list[str]], state: str) -> CallbackResult:
    return oauth_callback(query, state, "Spotify")


def _js_string(value: str) -> str:
    """A JSON string literal that cannot end a script element or open a tag."""
    return (
        json.dumps(value)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def apple_sign_in_page(developer_token: str, port: int) -> str:
    token_url = f"http://127.0.0.1:{port}/token"
    return f"""<!doctype html>
<html>
<head><meta charset="utf-8"><title>LinerSync live sign-in</title></head>
<body>
<p id="status">Loading MusicKit...</p>
<button id="go" disabled>Sign in to Apple Music</button>
<script src="{MUSICKIT_URL}"></script>
<script>
const DEVELOPER_TOKEN = {_js_string(developer_token)};
const status = document.getElementById("status");
document.addEventListener("musickitloaded", async () => {{
  await MusicKit.configure({{
    developerToken: DEVELOPER_TOKEN,
    app: {{ name: "LinerSync live test", build: "1" }},
  }});
  const button = document.getElementById("go");
  button.disabled = false;
  status.textContent = "Ready.";
  button.onclick = async () => {{
    const userToken = await MusicKit.getInstance().authorize();
    await fetch("{token_url}", {{
      method: "POST",
      headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify({{ music_user_token: userToken }}),
    }});
    status.textContent = "Done. You can close this tab.";
  }};
}});
</script>
</body>
</html>
"""


def _serve(port: int, handler: type[BaseHTTPRequestHandler]) -> None:
    with HTTPServer(("127.0.0.1", port), handler) as server:
        while not getattr(server, "finished", False):
            server.handle_request()


def _reply(h: BaseHTTPRequestHandler, status: int, body: str, kind: str) -> None:
    data = body.encode()
    h.send_response(status)
    h.send_header("Content-Type", f"{kind}; charset=utf-8")
    h.send_header("Content-Length", str(len(data)))
    h.end_headers()
    h.wfile.write(data)


def _loopback_oauth(
    plugin: OAuthSetup,
    service: ServiceId,
    display_name: str,
    login_url: str,
    port: int,
    verifier: str,
    state: str,
    client_id: str,
) -> dict[str, str]:
    """Run one loopback sign-in; returns the credentials it saved."""
    results: list[CallbackResult] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parts = urlsplit(self.path)
            if parts.path != "/callback":
                _reply(self, 404, "Not found", "text/plain")
                return
            result = oauth_callback(parse_qs(parts.query), state, display_name)
            _reply(self, result.status, result.message, "text/plain")
            if result.finished:
                results.append(result)
                self.server.finished = True  # type: ignore[attr-defined]

        def log_message(self, format: str, *args: object) -> None:
            return None

    print(f"Opening {login_url.split('?')[0]} ...")
    webbrowser.open(login_url)
    _serve(port, Handler)
    result = results[0]
    if result.code is None:
        raise LoginFailed(result.message)

    credentials = InMemoryCredentials()
    credentials.save(service, {"client_id": client_id})
    ctx = ConnectorContext(credentials, InMemoryQuota(), _SystemClock())
    asyncio.run(
        plugin.complete(
            ctx, _redirect_uri(port), result.code, {"code_verifier": verifier}
        )
    )
    return credentials.get(service)


def _login_spotify(client_id: str, port: int) -> list[str]:
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(16)
    url = spotify_login_url(client_id, port, state, challenge)
    saved = _loopback_oauth(
        SPOTIFY_PLUGIN,
        ServiceId("spotify"),
        "Spotify",
        url,
        port,
        verifier,
        state,
        client_id,
    )
    return [
        f"export SPOTIFY_CLIENT_ID={client_id}",
        f"export SPOTIFY_REFRESH_TOKEN={saved['refresh_token']}",
        "export LINERSYNC_LIVE=1",
    ]


def _login_tidal(client_id: str, port: int) -> list[str]:
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(16)
    url = tidal_login_url(client_id, port, state, challenge)
    saved = _loopback_oauth(
        TIDAL_PLUGIN,
        ServiceId("tidal"),
        "Tidal",
        url,
        port,
        verifier,
        state,
        client_id,
    )
    return [
        f"export TIDAL_CLIENT_ID={client_id}",
        f"export TIDAL_REFRESH_TOKEN={saved['refresh_token']}",
        "export LINERSYNC_LIVE=1",
    ]


def _login_youtube_music(client_id: str, client_secret: str) -> list[str]:
    """Google's device flow: no local server, just a code to enter."""
    service = ServiceId("youtube_music")
    plugin = YouTubeMusicPlugin(YTAuthMode.OAUTH)
    credentials = InMemoryCredentials()
    credentials.save(service, {"client_id": client_id, "client_secret": client_secret})
    ctx = ConnectorContext(credentials, InMemoryQuota(), _SystemClock())

    async def flow() -> str:
        try:
            start = await plugin.start_device_code(ctx)
        except ValueError as e:
            raise LoginFailed(str(e)) from None
        print(f"Open {start.verification_url} and enter {start.user_code}")
        while True:
            await asyncio.sleep(start.interval_s)
            poll = await plugin.poll_device_code(ctx, start.keep)
            if poll.status == "done":
                return credentials.get(service)["refresh_token"]
            if poll.status != "pending":
                raise LoginFailed(poll.message or "Sign-in didn't finish")

    refresh = asyncio.run(flow())
    return [
        "export LINERSYNC_YTMUSIC_AUTH=oauth",
        f"export YTMUSIC_CLIENT_ID={client_id}",
        f"export YTMUSIC_CLIENT_SECRET={client_secret}",
        f"export YTMUSIC_REFRESH_TOKEN={refresh}",
        "export LINERSYNC_LIVE=1",
    ]


def _login_apple(team_id: str, key_id: str, key_file: Path, port: int) -> list[str]:
    token = make_developer_token(
        team_id, key_id, key_file.read_text(), datetime.now(UTC)
    )
    page = apple_sign_in_page(token, port)
    found: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/":
                _reply(self, 200, page, "text/html")
            else:
                _reply(self, 404, "Not found", "text/plain")

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            try:
                body = json.loads(self.rfile.read(length))
                user_token = str(body["music_user_token"])
            except (ValueError, KeyError, TypeError):
                _reply(self, 400, "Bad request", "text/plain")
                return
            found["token"] = user_token
            _reply(self, 200, "ok", "text/plain")
            self.server.finished = True  # type: ignore[attr-defined]

        def log_message(self, format: str, *args: object) -> None:
            return None

    print(f"Opening http://127.0.0.1:{port}/ ...")
    webbrowser.open(f"http://127.0.0.1:{port}/")
    _serve(port, Handler)
    return [
        f"export APPLE_TEAM_ID={team_id}",
        f"export APPLE_KEY_ID={key_id}",
        f"export APPLE_PRIVATE_KEY_PATH={key_file.resolve()}",
        f"export APPLE_MUSIC_USER_TOKEN={found['token']}",
        "export LINERSYNC_LIVE=1",
    ]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="live_login")
    sub = parser.add_subparsers(dest="service", required=True)
    sp = sub.add_parser("spotify")
    sp.add_argument("--client-id", required=True)
    sp.add_argument("--port", type=int, default=8765)
    td = sub.add_parser("tidal")
    td.add_argument("--client-id", required=True)
    td.add_argument("--port", type=int, default=8767)
    yt = sub.add_parser("youtube-music")
    yt.add_argument("--client-id", required=True)
    yt.add_argument("--client-secret", required=True)
    ap = sub.add_parser("apple-music")
    ap.add_argument("--team-id", required=True)
    ap.add_argument("--key-id", required=True)
    ap.add_argument("--key-file", type=Path, required=True)
    ap.add_argument("--port", type=int, default=8766)
    args = parser.parse_args(argv)
    try:
        if args.service == "spotify":
            lines = _login_spotify(args.client_id, args.port)
        elif args.service == "tidal":
            lines = _login_tidal(args.client_id, args.port)
        elif args.service == "youtube-music":
            lines = _login_youtube_music(args.client_id, args.client_secret)
        else:
            lines = _login_apple(args.team_id, args.key_id, args.key_file, args.port)
    except LoginFailed as e:
        print(str(e), file=sys.stderr)
        return 1
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
