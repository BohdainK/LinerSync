"""Shared HTTP layer: safe retries, rate limits, quota accounting, auth refresh.

Safety rule: a request that may have reached the server and changed state is
never retried unless it is idempotent or the response proves it was rejected.
"""

import asyncio
import random as _random
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC
from email.utils import parsedate_to_datetime
from types import TracebackType
from typing import Any, Protocol, Self

import httpx

from linersync.adapters.connectors.kit.ratelimit import RateLimiter
from linersync.domain.tracks import ServiceId
from linersync.ports.errors import ReadFailed, WriteFailed
from linersync.ports.quota import QuotaPolicy, QuotaRecorder, quota_day_start
from linersync.ports.runtime import Clock

# Failures where the request provably never reached the server.
_NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
_BODY_LIMIT = 200


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 4
    base_delay: float = 0.5
    max_delay: float = 30.0
    max_retry_after: float = 60.0


@dataclass(frozen=True, slots=True)
class HttpSettings:
    timeout: float = 30.0
    rate_per_second: float = 5.0
    burst: int = 10
    retry: RetryPolicy = field(default_factory=RetryPolicy)


class AuthProvider(Protocol):
    async def headers(self) -> Mapping[str, str]: ...

    async def refresh(self) -> bool:
        """True: new credentials, retry once.

        May raise HttpFailure(auth_required=True).
        """
        ...


class HttpFailure(Exception):
    def __init__(
        self,
        service: ServiceId,
        message: str,
        *,
        status: int | None = None,
        maybe_applied: bool,
        auth_required: bool = False,
        quota_exhausted: bool = False,
    ) -> None:
        super().__init__(message)
        self.service = service
        self.message = message
        self.status = status
        self.maybe_applied = maybe_applied
        self.auth_required = auth_required
        self.quota_exhausted = quota_exhausted


def read_failed(e: HttpFailure) -> ReadFailed:
    return ReadFailed(e.service, e.message, auth_required=e.auth_required)


def write_failed(e: HttpFailure, *, maybe_applied: bool | None = None) -> WriteFailed:
    return WriteFailed(
        e.service,
        e.message,
        e.maybe_applied if maybe_applied is None else maybe_applied,
        auth_required=e.auth_required,
    )


def nothing_sent(e: HttpFailure | ReadFailed) -> WriteFailed:
    """A write that failed while reading before it, so nothing was sent."""
    return WriteFailed(e.service, e.message, False, auth_required=e.auth_required)


def json_object(response: httpx.Response) -> Mapping[str, Any]:
    """The response body, which must be a JSON object; raises ValueError."""
    body = response.json()
    if not isinstance(body, Mapping):
        raise ValueError("not a JSON object")
    return body


async def apply_in_batches[T](
    items: Sequence[T], size: int, send: Callable[[Sequence[T]], Awaitable[None]]
) -> None:
    """Send chunks in order. A failure after the first chunk is `maybe_applied`,
    because earlier chunks have landed."""
    if size < 1:
        raise ValueError("size must be >= 1")
    for start in range(0, len(items), size):
        try:
            await send(items[start : start + size])
        except HttpFailure as e:
            if start > 0:
                e.maybe_applied = True
            raise


class HttpClient:
    def __init__(
        self,
        service: ServiceId,
        base_url: str,
        *,
        settings: HttpSettings,
        quota: QuotaRecorder,
        quota_policy: QuotaPolicy,
        clock: Clock,
        auth: AuthProvider | None = None,
        limiter: RateLimiter | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        random: Callable[[], float] = _random.random,
        is_quota_exceeded: Callable[[httpx.Response], bool] = lambda r: False,
        proven_rejection: Callable[[httpx.Response], bool] = (
            lambda r: r.status_code == 429
        ),
    ) -> None:
        self._service = service
        self._settings = settings
        self._quota = quota
        self._quota_policy = quota_policy
        self._clock = clock
        self._auth = auth
        if limiter is None:
            limiter = RateLimiter(settings.rate_per_second, settings.burst, sleep=sleep)
        self._limiter = limiter
        self._sleep = sleep
        self._random = random
        self._is_quota_exceeded = is_quota_exceeded
        self._proven_rejection = proven_rejection
        self._client = httpx.AsyncClient(
            base_url=base_url, timeout=settings.timeout, transport=transport
        )

    @property
    def has_auth(self) -> bool:
        return self._auth is not None

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def request(
        self,
        method: str,
        url: str,
        *,
        idempotent: bool,
        params: Mapping[str, str | int] | None = None,
        json: Any = None,
        data: Mapping[str, str] | None = None,
        content: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        cost: int = 1,
    ) -> httpx.Response:
        """Return the response if it is 2xx; otherwise raise HttpFailure."""
        retry = self._settings.retry
        path = httpx.URL(url).path  # never the query: it may carry secrets
        prefix = f"{method} {path} failed"
        refreshed = False
        # Sticky: once any attempt may have applied, the request may have applied.
        landed = False
        attempt = 0
        while True:
            attempt += 1
            self._check_quota(prefix, cost, landed)
            await self._limiter.acquire()
            send_headers: dict[str, str] = {}
            if self._auth is not None:
                auth_headers = self._auth.headers()
                send_headers.update(await self._keep_landed(auth_headers, landed))
            if headers:
                send_headers.update(headers)

            response: httpx.Response | None = None
            try:
                response = await self._client.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    data=data,
                    content=content,
                    headers=send_headers,
                )
            except httpx.TransportError as exc:
                sent = not isinstance(exc, _NOT_SENT)
                if sent:
                    self._record(cost)
                landed = landed or sent
                failure = HttpFailure(
                    self._service,
                    f"{prefix}: {type(exc).__name__}",
                    maybe_applied=landed,
                )
                retryable = True
            except httpx.RequestError as exc:
                # Not a transport failure (e.g. an undecodable body, a
                # redirect loop): the request was sent and may have applied.
                self._record(cost)
                raise HttpFailure(
                    self._service,
                    f"{prefix}: {type(exc).__name__}",
                    maybe_applied=True,
                ) from None
            else:
                self._record(cost)
                if response.is_success:
                    return response
                status = response.status_code
                message = _status_message(prefix, response)
                if status == 401:
                    if self._auth is not None and not refreshed:
                        refreshed = True
                        if await self._keep_landed(self._auth.refresh(), landed):
                            attempt -= 1  # a credentials retry is not a failed attempt
                            continue
                    raise HttpFailure(
                        self._service,
                        message,
                        status=status,
                        maybe_applied=landed,
                        auth_required=True,
                    )
                if status == 429 and self._is_quota_exceeded(response):
                    raise HttpFailure(
                        self._service,
                        message,
                        status=status,
                        maybe_applied=landed,
                        quota_exhausted=True,
                    )
                if 400 <= status < 500:
                    applied = False
                elif 500 <= status < 600:
                    applied = not self._proven_rejection(response)
                else:  # unexpected 1xx/3xx: the server handled it somehow
                    applied = True
                landed = landed or applied
                failure = HttpFailure(
                    self._service, message, status=status, maybe_applied=landed
                )
                retryable = status == 429 or 500 <= status < 600

            if not retryable or attempt >= retry.max_attempts:
                raise failure
            if not idempotent and failure.maybe_applied:
                raise failure

            retry_after = _retry_after(response, self._clock) if response else None
            if retry_after is not None:
                if retry_after > retry.max_retry_after:
                    raise failure
                wait = retry_after
                if response is not None and response.status_code == 429:
                    self._limiter.pause_for(wait)
            else:
                d = min(retry.max_delay, retry.base_delay * 2 ** (attempt - 1))
                wait = d / 2 + d / 2 * self._random()
            await self._sleep(wait)

    @staticmethod
    async def _keep_landed[T](call: Awaitable[T], landed: bool) -> T:
        """Await an auth call; an HttpFailure from it keeps the sticky flag."""
        try:
            return await call
        except HttpFailure as e:
            e.maybe_applied = e.maybe_applied or landed
            raise

    def _check_quota(self, prefix: str, cost: int, landed: bool) -> None:
        limit = self._quota_policy.daily_limit
        if limit is None:
            return
        since = quota_day_start(self._clock.now(), self._quota_policy)
        if self._quota.used_since(self._service, since) + cost > limit:
            raise HttpFailure(
                self._service,
                f"{prefix}: daily quota exhausted",
                maybe_applied=landed,
                quota_exhausted=True,
            )

    def _record(self, cost: int) -> None:
        self._quota.record(self._service, cost, self._clock.now())


def _status_message(prefix: str, response: httpx.Response) -> str:
    message = f"{prefix}: {response.status_code}"
    try:
        body = response.text.strip()
    except Exception:  # undecodable body: the status alone is enough
        body = ""
    if body:
        message += f" {body[:_BODY_LIMIT]}"
    return message


def _retry_after(response: httpx.Response, clock: Clock) -> float | None:
    """Seconds to wait from a Retry-After header (delta-seconds or HTTP-date)."""
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    raw = raw.strip()
    if raw.isdigit():
        return float(raw)
    try:
        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - clock.now()).total_seconds())
