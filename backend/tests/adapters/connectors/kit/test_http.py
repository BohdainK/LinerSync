from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import Any

import httpx
import pytest

from linersync.adapters.connectors.kit.http import (
    AuthProvider,
    HttpClient,
    HttpFailure,
    HttpSettings,
    apply_in_batches,
    read_failed,
    write_failed,
)
from linersync.adapters.connectors.kit.ratelimit import RateLimiter
from linersync.adapters.memory.quota import InMemoryQuota
from linersync.adapters.memory.runtime import FixedClock
from linersync.domain.tracks import ServiceId
from linersync.ports.errors import ConnectorError, ReadFailed, WriteFailed
from linersync.ports.quota import QuotaPolicy, quota_day_start

SP = ServiceId("spotify")
NOW = datetime(2026, 10, 26, 12, tzinfo=UTC)
SECRET = "s3cret-token-value"

Headers = Mapping[str, str]
Step = int | tuple[int, Headers] | tuple[int, Headers, str] | Exception


class Script:
    def __init__(self, steps: Sequence[Step]) -> None:
        self.steps = list(steps)
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        if isinstance(step, int):
            return httpx.Response(step)
        if len(step) == 2:
            return httpx.Response(step[0], headers=dict(step[1]))
        return httpx.Response(step[0], headers=dict(step[1]), text=step[2])


class FakeTime:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@dataclass
class FakeAuth:
    refresh_result: bool = True
    token: str = SECRET
    refreshes: int = 0

    async def headers(self) -> Mapping[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    async def refresh(self) -> bool:
        self.refreshes += 1
        if self.refresh_result:
            self.token = f"{SECRET}-new"
        return self.refresh_result


@dataclass
class Env:
    script: Script
    client: HttpClient
    time: FakeTime
    quota: InMemoryQuota

    def used(self) -> int:
        return self.quota.used_since(SP, quota_day_start(NOW, QuotaPolicy()))


def make(
    steps: Sequence[Step],
    *,
    auth: AuthProvider | None = None,
    policy: QuotaPolicy | None = None,
    quota: InMemoryQuota | None = None,
    **kwargs: Any,
) -> Env:
    script = Script(steps)
    t = FakeTime()
    q = quota if quota is not None else InMemoryQuota()
    client = HttpClient(
        SP,
        "https://api.example.test",
        settings=HttpSettings(),
        quota=q,
        quota_policy=policy if policy is not None else QuotaPolicy(),
        clock=FixedClock(NOW),
        auth=auth,
        limiter=RateLimiter(5, 10, monotonic=t.monotonic, sleep=t.sleep),
        transport=httpx.MockTransport(script.handler),
        sleep=t.sleep,
        random=lambda: 1.0,
        **kwargs,
    )
    return Env(script, client, t, q)


async def test_get_retries_5xx_then_succeeds() -> None:
    env = make([500, 502, 200])
    resp = await env.client.request("GET", "/v1/me", idempotent=True)
    assert resp.status_code == 200
    assert env.time.sleeps == [0.5, 1.0]
    assert env.used() == 3


async def test_get_gives_up_after_max_attempts() -> None:
    env = make([503] * 4)
    with pytest.raises(HttpFailure) as ei:
        await env.client.request("GET", "/v1/me", idempotent=True)
    assert ei.value.status == 503
    assert len(env.time.sleeps) == 3
    assert len(env.script.requests) == 4


async def test_post_not_retried_on_5xx() -> None:
    env = make([500, 201])
    with pytest.raises(HttpFailure) as ei:
        await env.client.request("POST", "/v1/x", idempotent=False, json={"a": 1})
    assert ei.value.maybe_applied is True
    assert len(env.script.requests) == 1
    assert env.time.sleeps == []


async def test_post_not_retried_on_read_timeout() -> None:
    env = make([httpx.ReadTimeout("slow"), 201])
    with pytest.raises(HttpFailure) as ei:
        await env.client.request("POST", "/v1/x", idempotent=False)
    assert ei.value.maybe_applied is True
    assert ei.value.status is None
    assert len(env.script.requests) == 1
    assert env.used() == 1


async def test_post_retried_on_connect_error() -> None:
    env = make([httpx.ConnectError("refused"), 201])
    resp = await env.client.request("POST", "/v1/x", idempotent=False)
    assert resp.status_code == 201
    assert len(env.script.requests) == 2
    assert env.used() == 1


async def test_post_retried_on_429_honours_retry_after() -> None:
    env = make([(429, {"Retry-After": "2"}), 201])
    resp = await env.client.request("POST", "/v1/x", idempotent=False)
    assert resp.status_code == 201
    assert env.time.sleeps == [2.0]


async def test_retry_after_http_date() -> None:
    when = format_datetime(NOW + timedelta(seconds=5), usegmt=True)
    env = make([(429, {"Retry-After": when}), 200])
    resp = await env.client.request("GET", "/v1/x", idempotent=True)
    assert resp.status_code == 200
    assert env.time.sleeps == [5.0]


async def test_retry_after_too_long_fails() -> None:
    env = make([(429, {"Retry-After": "120"}), 201])
    with pytest.raises(HttpFailure) as ei:
        await env.client.request("POST", "/v1/x", idempotent=False)
    assert ei.value.status == 429
    assert ei.value.maybe_applied is False
    assert env.time.sleeps == []


async def test_quota_429_not_retried() -> None:
    env = make([429, 200], is_quota_exceeded=lambda r: True)
    with pytest.raises(HttpFailure) as ei:
        await env.client.request("GET", "/v1/x", idempotent=True)
    assert ei.value.quota_exhausted is True
    assert ei.value.maybe_applied is False
    assert len(env.script.requests) == 1


async def test_503_write_retried_only_with_proven_rejection_hook() -> None:
    plain = make([503, 201])
    with pytest.raises(HttpFailure) as ei:
        await plain.client.request("POST", "/v1/x", idempotent=False)
    assert ei.value.maybe_applied is True
    assert len(plain.script.requests) == 1

    hooked = make([503, 201], proven_rejection=lambda r: r.status_code in (429, 503))
    resp = await hooked.client.request("POST", "/v1/x", idempotent=False)
    assert resp.status_code == 201
    assert hooked.time.sleeps == [0.5]


async def test_401_refreshes_once_then_retries() -> None:
    auth = FakeAuth()
    env = make([401, 201], auth=auth)
    resp = await env.client.request("POST", "/v1/x", idempotent=False)
    assert resp.status_code == 201
    assert auth.refreshes == 1
    assert env.script.requests[0].headers["Authorization"] == f"Bearer {SECRET}"
    assert env.script.requests[1].headers["Authorization"] == f"Bearer {SECRET}-new"
    assert env.time.sleeps == []


async def test_401_after_refresh_is_auth_required() -> None:
    auth = FakeAuth()
    env = make([401, 401, 200], auth=auth)
    with pytest.raises(HttpFailure) as ei:
        await env.client.request("GET", "/v1/x", idempotent=True)
    assert ei.value.auth_required is True
    assert ei.value.maybe_applied is False
    assert auth.refreshes == 1
    assert len(env.script.requests) == 2


async def test_401_without_refresh_is_auth_required() -> None:
    auth = FakeAuth(refresh_result=False)
    env = make([401, 200], auth=auth)
    with pytest.raises(HttpFailure) as ei:
        await env.client.request("GET", "/v1/x", idempotent=True)
    assert ei.value.auth_required is True
    assert ei.value.maybe_applied is False
    assert len(env.script.requests) == 1

    no_auth = make([401, 200])
    with pytest.raises(HttpFailure) as ei:
        await no_auth.client.request("GET", "/v1/x", idempotent=True)
    assert ei.value.auth_required is True
    assert len(no_auth.script.requests) == 1


async def test_daily_quota_blocks_before_sending() -> None:
    quota = InMemoryQuota()
    quota.record(SP, 5, NOW - timedelta(days=1))
    quota.record(SP, 2, NOW)
    env = make([200], policy=QuotaPolicy(daily_limit=2), quota=quota)
    with pytest.raises(HttpFailure) as ei:
        await env.client.request("GET", "/v1/x", idempotent=True)
    assert ei.value.quota_exhausted is True
    assert ei.value.maybe_applied is False
    assert env.script.requests == []


async def test_daily_quota_allows_within_limit() -> None:
    quota = InMemoryQuota()
    quota.record(SP, 5, NOW - timedelta(days=1))
    env = make([200], policy=QuotaPolicy(daily_limit=2), quota=quota)
    resp = await env.client.request("GET", "/v1/x", idempotent=True, cost=2)
    assert resp.status_code == 200
    assert env.used() == 2


async def test_4xx_not_retried() -> None:
    env = make([404, 200])
    with pytest.raises(HttpFailure) as ei:
        await env.client.request("GET", "/v1/x", idempotent=True)
    assert ei.value.status == 404
    assert ei.value.maybe_applied is False
    assert len(env.script.requests) == 1


async def test_maybe_applied_sticks_across_attempts() -> None:
    # An idempotent write that may have landed stays "maybe applied" even if
    # a later attempt is cleanly rejected.
    env = make([500, 404])
    with pytest.raises(HttpFailure) as ei:
        await env.client.request("PUT", "/v1/x", idempotent=True)
    assert ei.value.status == 404
    assert ei.value.maybe_applied is True


async def test_failure_message_has_no_secrets() -> None:
    env = make([(500, {}, "oops")], auth=FakeAuth())
    with pytest.raises(HttpFailure) as ei:
        await env.client.request(
            "POST", "/v1/x?token=abc", idempotent=False, headers={"X-Key": SECRET}
        )
    text = str(ei.value)
    assert SECRET not in text
    assert "abc" not in text
    assert text.startswith("POST /v1/x failed: 500")
    assert "oops" in text


async def test_failure_message_body_truncated() -> None:
    env = make([(400, {}, "y" * 500)])
    with pytest.raises(HttpFailure) as ei:
        await env.client.request("GET", "/v1/x", idempotent=True)
    assert str(ei.value).count("y") == 200


async def test_transport_error_message_names_exception() -> None:
    env = make([httpx.ReadTimeout("slow")])
    with pytest.raises(HttpFailure) as ei:
        await env.client.request("GET", "/v1/x", idempotent=False)
    assert str(ei.value) == "GET /v1/x failed: ReadTimeout"


def test_read_and_write_failed_mapping() -> None:
    e = HttpFailure(SP, "boom", status=401, maybe_applied=True, auth_required=True)
    r = read_failed(e)
    assert isinstance(r, ReadFailed)
    assert (r.service, r.message, r.auth_required) == (SP, "boom", True)

    w = write_failed(e)
    assert isinstance(w, WriteFailed)
    assert (w.service, w.message, w.auth_required, w.maybe_applied) == (
        SP,
        "boom",
        True,
        True,
    )
    assert write_failed(e, maybe_applied=False).maybe_applied is False

    assert ConnectorError(SP, "x").auth_required is False
    legacy = WriteFailed(SP, "m", True)
    assert legacy.maybe_applied is True
    assert legacy.auth_required is False


async def test_apply_in_batches_first_chunk_failure_keeps_flag() -> None:
    original = HttpFailure(SP, "nope", status=400, maybe_applied=False)
    sent: list[Sequence[int]] = []

    async def send(chunk: Sequence[int]) -> None:
        sent.append(chunk)
        raise original

    with pytest.raises(HttpFailure) as ei:
        await apply_in_batches([1, 2, 3], 2, send)
    assert ei.value is original
    assert ei.value.maybe_applied is False
    assert sent == [[1, 2]]


async def test_apply_in_batches_later_chunk_failure_is_maybe_applied() -> None:
    sent: list[Sequence[int]] = []

    async def send(chunk: Sequence[int]) -> None:
        sent.append(chunk)
        if len(sent) == 2:
            raise HttpFailure(SP, "nope", status=400, maybe_applied=False)

    with pytest.raises(HttpFailure) as ei:
        await apply_in_batches([1, 2, 3, 4, 5], 2, send)
    assert ei.value.maybe_applied is True
    assert ei.value.status == 400
    assert sent == [[1, 2], [3, 4]]


async def test_apply_in_batches_sends_all_chunks() -> None:
    sent: list[Sequence[int]] = []

    async def send(chunk: Sequence[int]) -> None:
        sent.append(chunk)

    await apply_in_batches([1, 2, 3, 4, 5], 2, send)
    assert sent == [[1, 2], [3, 4], [5]]


@pytest.mark.parametrize(
    "exc",
    [httpx.DecodingError("bad gzip"), httpx.TooManyRedirects("loop")],
    ids=["decoding", "redirects"],
)
async def test_non_transport_request_error_is_maybe_applied_not_retried(
    exc: httpx.RequestError,
) -> None:
    for method, idempotent in (("GET", True), ("POST", False)):
        env = make([exc, 200])
        with pytest.raises(HttpFailure) as ei:
            await env.client.request(method, "/v1/x", idempotent=idempotent)
        assert ei.value.maybe_applied is True
        assert ei.value.status is None
        assert type(exc).__name__ in ei.value.message
        assert len(env.script.requests) == 1
        assert env.time.sleeps == []


def _signed_out() -> HttpFailure:
    return HttpFailure(SP, "sign in again", maybe_applied=False, auth_required=True)


@dataclass
class FailingAuth:
    """Works for the first `ok_headers` calls, then fails like an expired login."""

    ok_headers: int = 1
    refresh_fails: bool = False
    calls: int = 0

    async def headers(self) -> Mapping[str, str]:
        self.calls += 1
        if self.calls > self.ok_headers:
            raise _signed_out()
        return {"Authorization": f"Bearer {SECRET}"}

    async def refresh(self) -> bool:
        if self.refresh_fails:
            raise _signed_out()
        return True


async def test_auth_headers_failure_on_retry_keeps_landed_flag() -> None:
    env = make([500, 200], auth=FailingAuth(ok_headers=1))
    with pytest.raises(HttpFailure) as ei:
        await env.client.request("PUT", "/v1/x", idempotent=True)
    assert ei.value.maybe_applied is True
    assert ei.value.auth_required is True
    assert len(env.script.requests) == 1


async def test_auth_refresh_failure_after_landed_attempt_keeps_landed_flag() -> None:
    env = make([500, 401, 200], auth=FailingAuth(ok_headers=99, refresh_fails=True))
    with pytest.raises(HttpFailure) as ei:
        await env.client.request("PUT", "/v1/x", idempotent=True)
    assert ei.value.maybe_applied is True
    assert ei.value.auth_required is True
    assert len(env.script.requests) == 2


async def test_auth_failure_before_anything_landed_is_not_applied() -> None:
    env = make([200], auth=FailingAuth(ok_headers=0))
    with pytest.raises(HttpFailure) as ei:
        await env.client.request("POST", "/v1/x", idempotent=False)
    assert ei.value.maybe_applied is False
    assert env.script.requests == []
