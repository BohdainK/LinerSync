import json
import re
from urllib.parse import parse_qs, urlsplit

import pytest

from linersync.adapters.connectors.spotify.auth import pkce_pair
from linersync.devtools import live_login
from linersync.devtools.live_login import (
    LoginFailed,
    apple_sign_in_page,
    spotify_callback,
    spotify_login_url,
)
from tests.live.conftest import LIVE_ENV, live_skip_reason

MUSICKIT = "https://js-cdn.music.apple.com/musickit/v3/musickit.js"


def test_spotify_login_url_uses_loopback_and_pkce() -> None:
    _, challenge = pkce_pair()
    url = spotify_login_url("c" * 32, 8765, "st", challenge)
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    assert parts.netloc == "accounts.spotify.com"
    assert query["redirect_uri"] == ["http://127.0.0.1:8765/callback"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"] == [challenge]
    assert query["state"] == ["st"]
    assert "client_secret" not in query


def test_apple_sign_in_page_embeds_token_safely() -> None:
    evil = 'a</script><script>alert(1)</script>&"\'b'
    page = apple_sign_in_page(evil, 8766)
    assert MUSICKIT in page
    assert "http://127.0.0.1:8766/token" in page
    # Only the page's own two script tags may appear.
    assert page.count("</script>") == page.count("<script") == 2
    assert "alert(1)</script>" not in page
    match = re.search(r"const DEVELOPER_TOKEN = (.*);", page)
    assert match is not None
    assert json.loads(match.group(1)) == evil


def test_live_tests_skip_without_env() -> None:
    for service, names in LIVE_ENV.items():
        reason = live_skip_reason(service, {})
        assert reason is not None
        assert "LINERSYNC_LIVE" in reason
        env = {"LINERSYNC_LIVE": "1", **dict.fromkeys(names, "x")}
        assert live_skip_reason(service, env) is None
        partial = {k: v for k, v in env.items() if k != names[0]}
        reason = live_skip_reason(service, partial)
        assert reason is not None
        assert names[0] in reason


def test_spotify_callback_with_code_finishes() -> None:
    result = spotify_callback({"state": ["st"], "code": ["abc"]}, "st")
    assert result.finished and result.status == 200
    assert (result.code, result.error) == ("abc", None)


def test_spotify_callback_error_finishes_with_error() -> None:
    query = {"state": ["st"], "error": ["access_denied"]}
    result = spotify_callback(query, "st")
    assert result.finished and result.status == 400
    assert result.code is None
    assert result.error == "access_denied"
    assert "access_denied" in result.message


def test_spotify_callback_wrong_state_keeps_waiting() -> None:
    for query in (
        {"state": ["other"], "code": ["abc"]},
        {"state": ["other"], "error": ["access_denied"]},
        {},
    ):
        result = spotify_callback(query, "st")
        assert not result.finished and result.status == 400


def test_main_reports_failed_login_with_nonzero_status(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def refused(client_id: str, port: int) -> list[str]:
        raise LoginFailed("Spotify sign-in failed: access_denied")

    monkeypatch.setattr(live_login, "_login_spotify", refused)
    assert live_login.main(["spotify", "--client-id", "c" * 32]) == 1
    out = capsys.readouterr()
    assert "Spotify sign-in failed: access_denied" in out.err
    assert "export" not in out.out
