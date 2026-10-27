"""Single-password authentication and the CSRF defence."""

import hashlib
import hmac
import math
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from linersync.server.state import get_services

SESSION_COOKIE = "linersync_session"
SESSION_LIFETIME = timedelta(days=30)
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class LoginThrottle:
    """Global limit on failed logins (behind a proxy every client shares an IP)."""

    def __init__(
        self, max_failures: int = 5, window: timedelta = timedelta(minutes=15)
    ) -> None:
        self._max = max_failures
        self._window = window
        self._failures: list[datetime] = []

    def _prune(self, now: datetime) -> None:
        self._failures = [t for t in self._failures if now - t < self._window]

    def retry_after(self, now: datetime) -> timedelta | None:
        self._prune(now)
        if len(self._failures) < self._max:
            return None
        return self._failures[-self._max] + self._window - now

    def failure(self, now: datetime) -> None:
        self._prune(now)
        self._failures.append(now)

    def success(self) -> None:
        self._failures.clear()


async def require_session(request: Request) -> None:
    services = get_services(request)
    token = request.cookies.get(SESSION_COOKIE)
    if not token or not services.sessions.valid(token, services.clock.now()):
        raise HTTPException(status_code=401, detail="Sign in first")


class SameOriginMiddleware:
    def __init__(self, app: ASGIApp, public_origin: str) -> None:
        self._app = app
        self._origin = public_origin

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in _SAFE_METHODS:
            await self._app(scope, receive, send)
            return
        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope["headers"]
        }
        origin = headers.get("origin")
        if origin is not None and origin != self._origin:
            response = JSONResponse(
                {"detail": "Cross-origin request refused"}, status_code=403
            )
            await response(scope, receive, send)
            return
        media_type = headers.get("content-type", "").split(";")[0].strip().lower()
        if media_type != "application/json":
            response = JSONResponse(
                {"detail": "Content-Type must be application/json"}, status_code=415
            )
            await response(scope, receive, send)
            return
        await self._app(scope, receive, send)


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


class LoginBody(BaseModel):
    password: str


router = APIRouter(prefix="/api/auth")


@router.post("/login", status_code=204)
async def login(body: LoginBody, request: Request) -> Response:
    services = get_services(request)
    now = services.clock.now()
    wait = services.throttle.retry_after(now)
    if wait is not None:
        minutes = max(1, math.ceil(wait.total_seconds() / 60))
        raise HTTPException(
            status_code=429, detail=f"Too many attempts. Try again in {minutes} minutes"
        )
    if not hmac.compare_digest(
        _digest(body.password), _digest(services.config.admin_password)
    ):
        services.throttle.failure(now)
        raise HTTPException(status_code=401, detail="Wrong password")
    services.throttle.success()
    token = services.sessions.create(now, SESSION_LIFETIME)
    response = Response(status_code=204)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(SESSION_LIFETIME.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=services.config.secure_cookies,
        path="/",
    )
    return response


@router.post("/logout", status_code=204)
async def logout(request: Request) -> Response:
    await require_session(request)
    services = get_services(request)
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        services.sessions.delete(token)
    response = Response(status_code=204)
    response.delete_cookie(
        SESSION_COOKIE,
        httponly=True,
        samesite="lax",
        secure=services.config.secure_cookies,
        path="/",
    )
    return response


@router.get("/session")
async def session(request: Request) -> dict[str, bool]:
    await require_session(request)
    return {"signed_in": True}
