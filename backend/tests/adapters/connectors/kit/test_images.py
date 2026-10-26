import base64
import io
import random

import pytest
from PIL import Image, ImageDraw

from linersync.adapters.connectors.kit.images import (
    ArtworkHasher,
    http_fetcher,
    jpeg_for_upload,
    phash_hex,
)
from tests.adapters.connectors.kit.test_http import FakeAuth, make


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _gradient(size: int) -> bytes:
    img = Image.new("RGB", (size, size))
    draw = ImageDraw.Draw(img)
    for x in range(size):
        draw.line(
            [(x, 0), (x, size)], fill=(x * 255 // size, 40, 200 - x * 150 // size)
        )
    draw.ellipse([size // 4, size // 4, size * 3 // 4, size // 2], fill=(250, 250, 20))
    return _png(img)


def _other(size: int) -> bytes:
    img = Image.new("RGB", (size, size), (10, 10, 10))
    draw = ImageDraw.Draw(img)
    for i in range(0, size, max(size // 4, 1)):
        draw.rectangle([i, 0, i + size // 8, size], fill=(240, 240, 240))
    draw.rectangle([0, size // 2, size, size * 5 // 8], fill=(200, 30, 30))
    return _png(img)


def _distance(a: str, b: str) -> int:
    return (int(a, 16) ^ int(b, 16)).bit_count()


def test_phash_stable_across_resize() -> None:
    big = Image.open(io.BytesIO(_gradient(300)))
    a = phash_hex(_png(big.resize((64, 64), Image.Resampling.LANCZOS)))
    b = phash_hex(_png(big))
    assert len(a) == 16
    assert _distance(a, b) <= 6


def test_phash_differs_for_different_images() -> None:
    assert _distance(phash_hex(_gradient(200)), phash_hex(_other(200))) > 6


async def test_hasher_returns_none_on_corrupt_bytes_and_fetch_error() -> None:
    async def fetch(url: str) -> bytes:
        if url == "bad":
            raise RuntimeError("boom")
        return b"not an image"

    hasher = ArtworkHasher(fetch)
    assert await hasher.hash("bad") is None
    assert await hasher.hash("corrupt") is None
    assert await hasher.hash(None) is None


async def test_hasher_fetches_each_url_once() -> None:
    calls: list[str] = []
    data = _gradient(64)

    async def fetch(url: str) -> bytes:
        calls.append(url)
        return data

    hasher = ArtworkHasher(fetch)
    out = await hasher.hash_many(["a", "b", "a", None])
    assert await hasher.hash("a") == out["a"]
    assert sorted(calls) == ["a", "b"]
    assert out["a"] is not None and out["a"] == out["b"]
    assert None not in out


async def test_hasher_does_not_cache_failures() -> None:
    attempts = 0

    async def fetch(url: str) -> bytes:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("transient")
        return _gradient(64)

    hasher = ArtworkHasher(fetch)
    assert await hasher.hash("a") is None
    assert await hasher.hash("a") is not None


def test_jpeg_for_upload_fits_and_decodes() -> None:
    rng = random.Random(1)
    img = Image.frombytes("RGB", (1200, 1200), rng.randbytes(1200 * 1200 * 3))
    out = jpeg_for_upload(_png(img))
    assert len(out) <= 190_000
    assert len(base64.b64encode(out)) <= 256_000
    decoded = Image.open(io.BytesIO(out))
    assert decoded.format == "JPEG"
    assert max(decoded.size) <= 640


def test_jpeg_for_upload_too_big_raises() -> None:
    with pytest.raises(ValueError):
        jpeg_for_upload(_gradient(300), max_bytes=100)


def test_http_fetcher_rejects_authenticated_client() -> None:
    env = make([200], auth=FakeAuth())
    with pytest.raises(ValueError):
        http_fetcher(env.client)


async def test_http_fetcher_sends_no_authorization() -> None:
    env = make([(200, {}, "img")])
    assert await http_fetcher(env.client)("https://cdn.example.test/a.jpg") == b"img"
    assert "authorization" not in env.script.requests[0].headers
