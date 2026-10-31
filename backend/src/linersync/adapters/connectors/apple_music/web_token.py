"""The developer token Apple's own web player (music.apple.com) uses.

Unofficial: Apple embeds a long-lived ES256 JWT in the web player's JavaScript
bundle and only accepts it with ``Origin: https://music.apple.com``. LinerSync
reads it from the bundle, so no Apple Developer Program membership is needed.
It breaks if Apple changes how the web player ships the token.
"""

import re
from datetime import UTC, datetime, timedelta

import jwt

from linersync.adapters.connectors.apple_music.parse import APPLE
from linersync.adapters.connectors.kit.http import HttpClient, HttpFailure
from linersync.ports.credentials import CredentialStore
from linersync.ports.runtime import Clock

WEB_PLAYER = "https://music.apple.com"
WEB_HEADERS = {"Origin": WEB_PLAYER, "Referer": WEB_PLAYER + "/"}
_BROWSER = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15"
}
# Redirect-free: "/" answers 301, and HttpClient does not follow redirects.
_PAGE = WEB_PLAYER + "/us/new"
_BUNDLE = re.compile(r'src="(/assets/index[~-][^"]+\.js)"')
# Any JSON header ("eyJ" is '{"'); Apple's header order has changed before.
_JWT = re.compile(r"eyJ[\w-]+\.[\w-]+\.[\w-]+")
# The bundle also carries tokens for other Apple apps; only this one is the
# web player's.
WEB_PLAYER_KID = "WebPlayKid"
RENEW_BEFORE = timedelta(days=1)
_CANT_FIND = "Couldn't find Apple Music's web player token"


def token_expires(token: str) -> datetime | None:
    try:
        claims = jwt.decode(token, options={"verify_signature": False})
    except jwt.PyJWTError:
        return None
    exp = claims.get("exp")
    if not isinstance(exp, int | float):
        return None
    return datetime.fromtimestamp(exp, UTC)


def _kid(token: str) -> object:
    try:
        return jwt.get_unverified_header(token).get("kid")
    except jwt.PyJWTError:
        return None


def find_token(bundle: str, now: datetime) -> str | None:
    """The web player's unexpired JWT in the bundle that lives longest."""
    best: tuple[datetime, str] | None = None
    for match in _JWT.finditer(bundle):
        if _kid(match.group()) != WEB_PLAYER_KID:
            continue
        expires = token_expires(match.group())
        if (
            expires is not None
            and expires > now
            and (best is None or expires > best[0])
        ):
            best = (expires, match.group())
    return best[1] if best else None


def _failure(message: str) -> HttpFailure:
    return HttpFailure(APPLE, message, maybe_applied=False, auth_required=True)


async def _get(web: HttpClient, url: str) -> str:
    """The web player's status must not reach callers: a 404 here is not a
    missing playlist. A 4xx means the web player changed; anything else may
    pass, so it stays retryable."""
    try:
        return (await web.request("GET", url, idempotent=True, headers=_BROWSER)).text
    except HttpFailure as e:
        if e.status is not None and 400 <= e.status < 500:
            raise _failure(f"{_CANT_FIND} ({e.message})") from None
        raise HttpFailure(
            APPLE, f"{_CANT_FIND} ({e.message})", maybe_applied=False
        ) from None


async def scrape_web_token(web: HttpClient, now: datetime) -> str:
    match = _BUNDLE.search(await _get(web, _PAGE))
    if match is None:
        raise _failure(_CANT_FIND)
    token = find_token(await _get(web, WEB_PLAYER + match.group(1)), now)
    if token is None:
        raise _failure(_CANT_FIND)
    return token


class WebTokens:
    """The web player token, cached in the credential store (``web_token``) so a
    restart or a new connector doesn't download the web player again."""

    def __init__(
        self, credentials: CredentialStore, clock: Clock, web: HttpClient
    ) -> None:
        self._credentials = credentials
        self._clock = clock
        self._web = web

    async def current(self) -> str:
        token = self._credentials.get(APPLE).get("web_token", "")
        expires = token_expires(token) if token else None
        if expires is None or expires - self._clock.now() < RENEW_BEFORE:
            return await self.force_renew()
        return token

    async def force_renew(self) -> str:
        token = await scrape_web_token(self._web, self._clock.now())
        self._credentials.save(APPLE, {"web_token": token})
        return token
