import hashlib
from datetime import UTC, datetime

from linersync.adapters.connectors.youtube_music.auth import (
    sapisid_from_cookie,
    sapisid_hash,
)


def test_sapisid_hash_follows_the_documented_formula() -> None:
    now = datetime(2026, 10, 30, 12, tzinfo=UTC)
    ts = int(now.timestamp())
    digest = hashlib.sha1(f"{ts} abc https://music.youtube.com".encode()).hexdigest()
    assert sapisid_hash("abc", now) == f"SAPISIDHASH {ts}_{digest}"


def test_sapisid_from_cookie() -> None:
    assert sapisid_from_cookie("SID=1; SAPISID=abc/def; HSID=2") == "abc/def"
    assert sapisid_from_cookie("Cookie: SID=1; SAPISID=abc") == "abc"
    assert sapisid_from_cookie("SID=1; __Secure-3PAPISID=xyz") == "xyz"
    assert sapisid_from_cookie("SID=1; HSID=2") is None
    assert sapisid_from_cookie("") is None


async def test_cookie_pasted_across_lines_is_one_header() -> None:
    from linersync.adapters.connectors.youtube_music.auth import CookieAuth
    from linersync.adapters.connectors.youtube_music.parse import SERVICE
    from linersync.adapters.memory.credentials import InMemoryCredentials
    from linersync.adapters.memory.runtime import FixedClock

    store = InMemoryCredentials()
    store.save(SERVICE, {"cookie": "Cookie: SID=1;\n  SAPISID=abc;\r\n\tHSID=2\n"})
    auth = CookieAuth(store, FixedClock(datetime(2026, 10, 30, 12, tzinfo=UTC)))
    headers = await auth.headers()
    assert headers["Cookie"] == "SID=1; SAPISID=abc; HSID=2"
    assert headers["Authorization"].startswith("SAPISIDHASH ")


async def test_cookie_auth_uses_the_chosen_signed_in_account() -> None:
    from linersync.adapters.connectors.youtube_music.auth import CookieAuth
    from linersync.adapters.connectors.youtube_music.parse import SERVICE
    from linersync.adapters.memory.credentials import InMemoryCredentials
    from linersync.adapters.memory.runtime import FixedClock

    store = InMemoryCredentials()
    store.save(SERVICE, {"cookie": "SAPISID=abc"})
    auth = CookieAuth(store, FixedClock(datetime(2026, 10, 30, 12, tzinfo=UTC)))
    assert (await auth.headers())["X-Goog-AuthUser"] == "0"
    store.save(SERVICE, {"auth_user": " 2 "})
    assert (await auth.headers())["X-Goog-AuthUser"] == "2"
