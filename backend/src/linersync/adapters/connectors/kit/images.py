"""Artwork helpers: perceptual hashing and cover-upload JPEG encoding."""

import asyncio
import io
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Iterable, Sequence

import imagehash
from PIL import Image

from linersync.adapters.connectors.kit.http import HttpClient

_MAX_SIDE = 640


def phash_hex(data: bytes) -> str:
    """8x8 perceptual hash as 16 hex chars (what the Plan 1 scorer expects)."""
    with Image.open(io.BytesIO(data)) as img:
        return str(imagehash.phash(img, hash_size=8))


def jpeg_for_upload(data: bytes, max_bytes: int = 190_000) -> bytes:
    """Re-encode as an RGB JPEG, longest side <= 640, at most `max_bytes`.

    `max_bytes` counts raw bytes. The default is chosen so that the base64
    payload (4/3 expansion) stays under Spotify's 256 KB limit.
    """
    with Image.open(io.BytesIO(data)) as src:
        img = src.convert("RGB")
    img.thumbnail((_MAX_SIDE, _MAX_SIDE))
    for quality in range(90, 20, -10):
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        if buf.tell() <= max_bytes:
            return buf.getvalue()
    raise ValueError(f"image cannot be encoded within {max_bytes} bytes")


class ArtworkHasher:
    def __init__(
        self,
        fetch: Callable[[str], Awaitable[bytes]],
        *,
        concurrency: int = 4,
        cache_size: int = 10_000,
    ) -> None:
        self._fetch = fetch
        self._sem = asyncio.Semaphore(concurrency)
        self._cache: OrderedDict[str, str] = OrderedDict()
        self._cache_size = cache_size

    async def hash(self, url: str | None) -> str | None:
        """Never raises: any fetch or decode failure gives None (not cached)."""
        if url is None:
            return None
        cached = self._cache.get(url)
        if cached is not None:
            self._cache.move_to_end(url)
            return cached
        try:
            async with self._sem:
                data = await self._fetch(url)
            value = phash_hex(data)
        except Exception:
            return None
        self._cache[url] = value
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return value

    async def hash_many(self, urls: Iterable[str | None]) -> dict[str, str | None]:
        unique = list(dict.fromkeys(u for u in urls if u is not None))
        values = await asyncio.gather(*(self.hash(u) for u in unique))
        return dict(zip(unique, values, strict=True))


async def hashes_for(
    hasher: ArtworkHasher | None, urls: Sequence[str | None]
) -> list[str | None]:
    """Each url's hash, in order; all None when artwork isn't hashed."""
    if hasher is None:
        return [None] * len(urls)
    hashes = await hasher.hash_many(urls)
    return [hashes[url] if url is not None else None for url in urls]


def http_fetcher(client: HttpClient) -> Callable[[str], Awaitable[bytes]]:
    """Fetch artwork bytes with a plain GET.

    `client` must be unauthenticated: artwork lives on CDN hosts, and an
    authenticated client would send its credentials to them. Use a separate
    client for artwork.
    """
    if client.has_auth:
        raise ValueError("http_fetcher requires an unauthenticated HttpClient")

    async def fetch(url: str) -> bytes:
        response = await client.request("GET", url, idempotent=True)
        return response.content

    return fetch
