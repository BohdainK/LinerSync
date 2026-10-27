from datetime import timedelta

import sqlalchemy as sa
from fastapi.testclient import TestClient

from linersync.adapters.memory.runtime import FixedClock
from linersync.adapters.sqlite.schema import sessions
from linersync.server.state import AppServices
from tests.server.conftest import AppFactory

PW = {"password": "pw"}


def test_health_needs_no_session(client: TestClient) -> None:
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["version"]


def test_session_endpoint_requires_login(client: TestClient) -> None:
    response = client.get("/api/auth/session")
    assert response.status_code == 401
    assert response.json() == {"detail": "Sign in first"}


def test_login_sets_httponly_lax_cookie(client: TestClient) -> None:
    response = client.post("/api/auth/login", json=PW)
    assert response.status_code == 204
    cookie = response.headers["set-cookie"]
    assert "linersync_session=" in cookie
    assert "HttpOnly" in cookie
    assert "samesite=lax" in cookie.lower()
    assert "Path=/" in cookie
    assert "Secure" not in cookie
    assert client.get("/api/auth/session").json() == {"signed_in": True}


def test_secure_cookie_for_https(app_factory: AppFactory) -> None:
    app = app_factory(public_url="https://sync.example.com")
    with TestClient(app) as client:
        response = client.post("/api/auth/login", json=PW)
    assert "Secure" in response.headers["set-cookie"]


def test_wrong_password_401(client: TestClient) -> None:
    response = client.post("/api/auth/login", json={"password": "nope"})
    assert response.status_code == 401
    assert response.json() == {"detail": "Wrong password"}
    assert "pw" not in response.text.replace("password", "")


def test_throttle_after_five_failures(client: TestClient, clock: FixedClock) -> None:
    for _ in range(5):
        assert client.post("/api/auth/login", json={"password": "x"}).status_code == 401
    blocked = client.post("/api/auth/login", json=PW)
    assert blocked.status_code == 429
    assert blocked.json()["detail"].startswith("Too many attempts. Try again in")
    clock.advance(timedelta(minutes=15))
    assert client.post("/api/auth/login", json=PW).status_code == 204


def test_session_expires_after_30_days(
    signed_in: TestClient, clock: FixedClock
) -> None:
    clock.advance(timedelta(days=29))
    assert signed_in.get("/api/auth/session").status_code == 200
    clock.advance(timedelta(days=2))
    assert signed_in.get("/api/auth/session").status_code == 401


def test_logout_invalidates_session(signed_in: TestClient) -> None:
    token = signed_in.cookies["linersync_session"]
    assert signed_in.post("/api/auth/logout", json={}).status_code == 204
    signed_in.cookies.set("linersync_session", token)
    assert signed_in.get("/api/auth/session").status_code == 401


def test_unsafe_request_needs_json_content_type(client: TestClient) -> None:
    response = client.post("/api/auth/login", data={"password": "pw"})
    assert response.status_code == 415
    ok = client.post(
        "/api/auth/login",
        content='{"password": "pw"}',
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    assert ok.status_code == 204


def test_cross_origin_post_forbidden(client: TestClient) -> None:
    evil = client.post(
        "/api/auth/login", json=PW, headers={"Origin": "https://evil.example"}
    )
    assert evil.status_code == 403
    same = client.post(
        "/api/auth/login", json=PW, headers={"Origin": "http://127.0.0.1:8000"}
    )
    assert same.status_code == 204


def test_session_token_stored_hashed(signed_in: TestClient) -> None:
    token = signed_in.cookies["linersync_session"]
    services: AppServices = signed_in.app.state.services  # type: ignore[attr-defined]
    with services.engine.connect() as conn:
        rows = conn.execute(sa.select(sessions)).all()
    assert len(rows) == 1
    assert token not in "".join(str(v) for v in rows[0])
