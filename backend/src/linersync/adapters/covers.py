"""Fetch playlist cover art over HTTPS (D5). Never raises: any failure is None."""

import asyncio
import hashlib
from http.cookiejar import CookieJar, DefaultCookiePolicy

import httpx

from linersync.domain.backups import COVER_MEDIA_TYPES, CoverImage

COVER_MAX_BYTES = 2_000_000
COVER_TIMEOUT_S = 10.0


class HttpCoverFetcher:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        # No auth; a jar whose policy accepts no domain keeps no cookies.
        self._client = client or httpx.AsyncClient(
            timeout=COVER_TIMEOUT_S,
            follow_redirects=True,
            cookies=CookieJar(DefaultCookiePolicy(allowed_domains=[])),
        )

    async def fetch(self, url: str) -> CoverImage | None:
        """https only (also after redirects); a 2xx body of an allowed image
        type, at most COVER_MAX_BYTES, within COVER_TIMEOUT_S in total."""
        try:
            if httpx.URL(url).scheme != "https":
                return None
            async with asyncio.timeout(COVER_TIMEOUT_S):
                return await self._fetch(url)
        except Exception:  # noqa: BLE001 - D5: any failure means no cover
            return None

    async def _fetch(self, url: str) -> CoverImage | None:
        async with self._client.stream("GET", url) as response:
            if not response.is_success or response.url.scheme != "https":
                return None
            media_type = (
                response.headers.get("content-type", "").split(";")[0].strip().lower()
            )
            if media_type not in COVER_MEDIA_TYPES:
                return None
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > COVER_MAX_BYTES:
                    return None
        body = bytes(data)
        return CoverImage(hashlib.sha256(body).hexdigest(), media_type, body)

    async def aclose(self) -> None:
        await self._client.aclose()
