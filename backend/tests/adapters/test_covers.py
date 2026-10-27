import asyncio
import hashlib
import time

import httpx
import pytest

from linersync.adapters import covers
from linersync.adapters.covers import COVER_MAX_BYTES, HttpCoverFetcher

URL = "https://img.example/cover.jpg"


def fetcher(handler: httpx.MockTransport) -> HttpCoverFetcher:
    return HttpCoverFetcher(httpx.AsyncClient(transport=handler))


async def test_fetches_image() -> None:
    data = b"\xff\xd8jpeg-bytes"

    def handle(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == URL
        return httpx.Response(
            200, content=data, headers={"Content-Type": "image/jpeg; charset=binary"}
        )

    f = fetcher(httpx.MockTransport(handle))
    cover = await f.fetch(URL)
    await f.aclose()
    assert cover is not None
    assert cover.data == data
    assert cover.media_type == "image/jpeg"
    assert cover.sha256 == hashlib.sha256(data).hexdigest()


async def test_rejects_http_scheme() -> None:
    calls: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, content=b"x", headers={"Content-Type": "image/png"})

    f = fetcher(httpx.MockTransport(handle))
    assert await f.fetch("http://img.example/cover.png") is None
    assert await f.fetch("not a url") is None
    assert calls == []


async def test_rejects_non_image() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"<html>", headers={"Content-Type": "text/html"}
        )

    assert await fetcher(httpx.MockTransport(handle)).fetch(URL) is None


async def test_rejects_oversize() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"x" * (COVER_MAX_BYTES + 1),
            headers={"Content-Type": "image/png"},
        )

    assert await fetcher(httpx.MockTransport(handle)).fetch(URL) is None


async def test_accepts_exactly_the_cap() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"x" * COVER_MAX_BYTES, headers={"Content-Type": "image/png"}
        )

    cover = await fetcher(httpx.MockTransport(handle)).fetch(URL)
    assert cover is not None
    assert len(cover.data) == COVER_MAX_BYTES


async def test_error_status_and_exception_give_none() -> None:
    def not_found(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=b"x", headers={"Content-Type": "image/png"})

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    assert await fetcher(httpx.MockTransport(not_found)).fetch(URL) is None
    assert await fetcher(httpx.MockTransport(boom)).fetch(URL) is None


async def test_default_client_keeps_no_cookies() -> None:
    f = HttpCoverFetcher()
    client = f._client  # noqa: SLF001
    assert client.timeout.read == 10.0
    assert client.follow_redirects is True
    assert client.auth is None
    response = httpx.Response(
        200,
        headers={"Set-Cookie": "sid=1; Domain=img.example; Path=/"},
        request=httpx.Request("GET", URL),
    )
    client.cookies.extract_cookies(response)
    assert len(client.cookies) == 0
    await f.aclose()


async def test_rejects_media_types_outside_the_raster_list() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        kind = request.url.params["t"]
        return httpx.Response(200, content=b"x", headers={"Content-Type": kind})

    f = fetcher(httpx.MockTransport(handle))
    for kind in ("image/svg+xml", "image/x-icon", "image/"):
        assert await f.fetch(f"{URL}?t={kind}") is None
    for kind in ("image/jpeg", "image/png", "image/webp", "image/gif"):
        cover = await f.fetch(f"{URL}?t={kind}")
        assert cover is not None and cover.media_type == kind


async def test_refuses_redirect_to_http() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.scheme == "https":
            return httpx.Response(
                302, headers={"Location": "http://img.example/cover.jpg"}
            )
        return httpx.Response(200, content=b"x", headers={"Content-Type": "image/png"})

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handle), follow_redirects=True
    )
    assert await HttpCoverFetcher(client).fetch(URL) is None


async def test_follows_redirect_to_https() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/cover.jpg":
            return httpx.Response(302, headers={"Location": "https://cdn.example/c"})
        return httpx.Response(200, content=b"x", headers={"Content-Type": "image/png"})

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handle), follow_redirects=True
    )
    assert await HttpCoverFetcher(client).fetch(URL) is not None


async def test_total_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(covers, "COVER_TIMEOUT_S", 0.05)

    async def handle(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, content=b"x", headers={"Content-Type": "image/png"})

    started = time.monotonic()
    assert await fetcher(httpx.MockTransport(handle)).fetch(URL) is None
    assert time.monotonic() - started < 2
