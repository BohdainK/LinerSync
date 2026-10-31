import json
from pathlib import Path

import httpx

from linersync.adapters.connectors.kit.recording import (
    DEFAULT_REDACT,
    RecordingTransport,
    redact,
)

SECRET = "tok-SECRET-123"
CODE = "auth-code-XYZ"


def _handler(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("cover.jpg"):
        return httpx.Response(
            200, content=b"\xff\xd8abc", headers={"content-type": "image/jpeg"}
        )
    return httpx.Response(
        200,
        json={"access_token": SECRET, "items": [{"Email": "me@x.io", "name": "ok"}]},
        headers={"content-type": "application/json", "retry-after": "3"},
    )


async def test_recording_redacts_tokens(tmp_path: Path) -> None:
    transport = RecordingTransport(httpx.MockTransport(_handler), tmp_path)
    async with httpx.AsyncClient(transport=transport) as client:
        r = await client.post(
            "https://api.example.com/v1/me/playlists",
            params={"code": CODE, "limit": "5"},
            headers={"Authorization": f"Bearer {SECRET}", "Music-User-Token": SECRET},
            json={"refresh_token": SECRET, "name": "mine"},
        )
        assert r.json()["access_token"] == SECRET  # caller still sees the real body
        await client.get("https://api.example.com/cover.jpg")
    files = sorted(tmp_path.glob("*.json"))
    assert [f.name for f in files] == [
        "001-POST-v1-me-playlists.json",
        "002-GET-cover-jpg.json",
    ]
    text = files[0].read_text()
    assert SECRET not in text and CODE not in text and "me@x.io" not in text
    data = json.loads(text)
    assert data["response"]["status"] == 200
    assert data["response"]["json"]["items"][0]["name"] == "ok"
    assert data["response"]["headers"]["retry-after"] == "3"
    assert data["request"]["json"]["name"] == "mine"
    assert "limit=5" in data["request"]["url"]
    image = json.loads(files[1].read_text())
    assert image["response"]["bytes"] == 5


def test_redact_recurses_case_insensitively() -> None:
    out = redact(
        {"A": [{"Client_Secret": "x", "k": 1}], "email": "e"},
        {"client_secret", "email"},
    )
    assert out == {"A": [{"Client_Secret": "REDACTED", "k": 1}], "email": "REDACTED"}


def test_default_redact_covers_tidal_profile_names() -> None:
    # Tidal usernames are often the account's email address.
    assert {"firstname", "lastname", "username"} <= {k.lower() for k in DEFAULT_REDACT}


def test_default_redact_covers_youtube_sign_in() -> None:
    wanted = {
        "cookie",
        "device_code",
        "user_code",
        "accountname",
        "client_secret",
        "channelhandle",
        "visitordata",
        "datasyncid",
    }
    assert wanted <= {k.lower() for k in DEFAULT_REDACT}


def test_default_redact_covers_qobuz_sign_in() -> None:
    # A Qobuz login is often the account's email address.
    assert {"user_auth_token", "login"} <= {k.lower() for k in DEFAULT_REDACT}


async def test_recording_scrubs_text_bodies(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/text":
            return httpx.Response(
                200,
                text="access_token=SECRET1&x=1",
                headers={"content-type": "text/plain"},
            )
        return httpx.Response(
            200,
            text='{"refresh_token": "SECRET2", broken',
            headers={"content-type": "application/json"},
        )

    transport = RecordingTransport(httpx.MockTransport(handler), tmp_path)
    async with httpx.AsyncClient(transport=transport) as client:
        await client.get("https://api.example.com/text")
        await client.get("https://api.example.com/bad")
    texts = [f.read_text() for f in sorted(tmp_path.glob("*.json"))]
    assert len(texts) == 2
    for t in texts:
        assert "SECRET1" not in t and "SECRET2" not in t
    assert "x=1" in texts[1] or "x=1" in texts[0]
