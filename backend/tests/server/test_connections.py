import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from linersync.adapters.connectors.kit.plugin import ConnectorPlugin
from linersync.adapters.connectors.spotify.plugin import SPOTIFY_PLUGIN
from linersync.adapters.fake.connector import FakeConnector
from linersync.adapters.fake.plugin import FakePlugin
from linersync.adapters.memory.runtime import FixedClock
from linersync.adapters.sqlite.repositories import SqliteSyncs
from linersync.domain.capabilities import Capability
from linersync.domain.playlists import PlaylistMeta
from linersync.domain.tracks import ServiceId
from linersync.server.connections import ConnectionManager, ConnectionStatus
from linersync.server.state import AppServices
from tests.factories import group, snapshot, track
from tests.server.conftest import AppFactory, drain
from tests.server.helpers import create, run_now
from tests.server.plugins import (
    SHOT,
    BrowserFakePlugin,
    DeviceFakePlugin,
    OAuthFakePlugin,
    ScreenshotPlugin,
    SecretFakePlugin,
    registry,
)

PUBLIC = "http://127.0.0.1:8000"
CALLBACK = "/api/connections/oauth/callback"


@contextmanager
def signed_in_client(
    app_factory: AppFactory, *plugins: ConnectorPlugin, **overrides: Any
) -> Iterator[TestClient]:
    app = app_factory(registry=registry(*plugins), **overrides)
    with TestClient(
        app, base_url="https://testserver", follow_redirects=False
    ) as client:
        login = client.post("/api/auth/login", json={"password": "pw"})
        assert login.status_code == 204
        yield client


def services(client: TestClient) -> AppServices:
    app: FastAPI = client.app  # type: ignore[assignment]
    found: AppServices = app.state.services
    return found


def manager(client: TestClient) -> ConnectionManager:
    return services(client).connections


def save(client: TestClient, service: str, **values: str) -> Any:
    return client.put(
        f"/api/connections/{service}/credentials", json={"values": values}
    )


def start_oauth(client: TestClient) -> str:
    save(client, "oauth_fake", client_id="cid")
    response = client.post("/api/connections/oauth_fake/oauth/start", json={})
    assert response.status_code == 200
    url: str = response.json()["url"]
    return parse_qs(urlsplit(url).query)["state"][0]


def sign_in_error(location: str) -> str:
    return parse_qs(urlsplit(location).query)["sign_in_error"][0]


def test_list_shows_not_set_up(app_factory: AppFactory) -> None:
    with signed_in_client(app_factory, SecretFakePlugin(), FakePlugin()) as client:
        body = client.get("/api/connections").json()
    assert body["load_errors"] == []
    assert [s["service"] for s in body["services"]] == ["fake", "secret_fake"]
    first = body["services"][0]
    assert first["status"] == "not_set_up"
    assert first["display_name"] == "Fake"
    assert first["values"] == {}
    assert first["secrets_set"] == []


def test_list_marks_experimental_plugins(app_factory: AppFactory) -> None:
    risky = SecretFakePlugin()
    risky.experimental = True  # type: ignore[attr-defined]
    with signed_in_client(app_factory, risky, FakePlugin()) as client:
        body = client.get("/api/connections").json()
        one = client.get("/api/connections/secret_fake").json()
    flags = {s["service"]: s["experimental"] for s in body["services"]}
    assert flags == {"fake": False, "secret_fake": True}
    assert one["experimental"] is True


def test_routes_need_a_session(app_factory: AppFactory) -> None:
    app = app_factory(registry=registry(FakePlugin()))
    with TestClient(app) as client:
        assert client.get("/api/connections").status_code == 401
        assert client.get("/api/connections/fake").status_code == 401


def test_unknown_service_is_404(app_factory: AppFactory) -> None:
    with signed_in_client(app_factory, FakePlugin()) as client:
        assert client.get("/api/connections/nope").status_code == 404
        assert client.get("/api/connections/nope/guide").status_code == 404
        assert save(client, "nope", account="a").status_code == 404
        assert client.post("/api/connections/nope/test", json={}).status_code == 404
        assert client.get("/api/connections/nope/playlists").status_code == 404
        response = client.request("DELETE", "/api/connections/nope", json={})
        assert response.status_code == 404


def test_save_values_validates(app_factory: AppFactory) -> None:
    with signed_in_client(app_factory, SecretFakePlugin()) as client:
        response = save(client, "secret_fake", account="me", token="nope")
        assert response.status_code == 422
        problems = response.json()["detail"]["problems"]
        assert problems["token"] == "Starts with tok-"
        info = client.get("/api/connections/secret_fake").json()
    assert info["status"] == "not_set_up"


def test_responses_never_contain_secret_values(app_factory: AppFactory) -> None:
    secret = "tok-SECRET1"
    with signed_in_client(app_factory, SecretFakePlugin()) as client:
        put = save(client, "secret_fake", account="me", token=secret)
        assert put.status_code == 200
        bodies = [
            put.text,
            client.get("/api/connections").text,
            client.get("/api/connections/secret_fake").text,
            client.get("/api/connections/secret_fake/guide").text,
            client.post("/api/connections/secret_fake/test", json={}).text,
            client.get("/api/connections/secret_fake/playlists").text,
        ]
        assert put.json()["secrets_set"] == ["token"]
        assert put.json()["values"] == {"account": "me"}
    for body in bodies:
        assert secret not in body


def test_blank_secret_keeps_stored_value(app_factory: AppFactory) -> None:
    with signed_in_client(app_factory, SecretFakePlugin()) as client:
        save(client, "secret_fake", account="me", token="tok-ONE")
        response = save(client, "secret_fake", account="  you  ", token="  ")
        assert response.status_code == 200
        assert response.json()["values"] == {"account": "you"}
        stored = services(client).credentials.get(ServiceId("secret_fake"))
    assert stored == {"account": "you", "token": "tok-ONE"}


def test_blank_secret_without_stored_value_is_required(
    app_factory: AppFactory,
) -> None:
    with signed_in_client(app_factory, SecretFakePlugin()) as client:
        response = save(client, "secret_fake", account="me", token="")
    assert response.status_code == 422
    assert response.json()["detail"]["problems"] == {"token": "Required"}


def test_unknown_field_rejected(app_factory: AppFactory) -> None:
    with signed_in_client(app_factory, SecretFakePlugin()) as client:
        response = save(client, "secret_fake", account="me", token="tok-A", extra="x")
        assert response.status_code == 422
        assert response.json()["detail"]["problems"] == {"extra": "Unknown field"}
        assert services(client).credentials.get(ServiceId("secret_fake")) == {}


def test_save_marks_untested(app_factory: AppFactory) -> None:
    with signed_in_client(app_factory, FakePlugin()) as client:
        info = save(client, "fake", account="me").json()
    assert info["status"] == "untested"
    assert info["values"] == {"account": "me"}


def test_test_marks_connected_with_account(
    app_factory: AppFactory, clock: FixedClock
) -> None:
    with signed_in_client(app_factory, FakePlugin()) as client:
        save(client, "fake", account="me")
        result = client.post("/api/connections/fake/test", json={}).json()
        info = client.get("/api/connections/fake").json()
    assert result == {"ok": True, "message": "Connected", "account_name": "fake"}
    assert info["status"] == "connected"
    assert info["account_name"] == "fake"
    assert info["message"] == "Connected"
    assert info["checked_at"] is not None


def test_failed_test_marks_failing(app_factory: AppFactory) -> None:
    class Failing(FakePlugin):
        async def test_connection(self, ctx: Any) -> Any:
            from linersync.adapters.connectors.kit.plugin import ConnectionTest

            return ConnectionTest(False, "Server unreachable")

    with signed_in_client(app_factory, Failing()) as client:
        save(client, "fake", account="me")
        result = client.post("/api/connections/fake/test", json={}).json()
        info = client.get("/api/connections/fake").json()
    assert result["ok"] is False
    assert info["status"] == "failing"
    assert info["message"] == "Server unreachable"


def test_oauth_round_trip(app_factory: AppFactory) -> None:
    plugin = OAuthFakePlugin()
    with signed_in_client(app_factory, plugin) as client:
        state = start_oauth(client)
        response = client.get(CALLBACK, params={"state": state, "code": "c"})
        info = client.get("/api/connections/oauth_fake").json()
        stored = services(client).credentials.get(ServiceId("oauth_fake"))
    assert response.status_code == 303
    assert (
        response.headers["location"] == f"{PUBLIC}/connections/oauth_fake?signed_in=1"
    )
    [call] = plugin.completed
    assert call.keep == {"verifier": "v"}
    assert call.code == "c"
    assert call.redirect_uri == f"{PUBLIC}{CALLBACK}"
    assert stored["refresh_token"] == "r"
    assert info["status"] == "untested"


def test_oauth_start_needs_setup_and_oauth(app_factory: AppFactory) -> None:
    with signed_in_client(app_factory, OAuthFakePlugin(), FakePlugin()) as client:
        not_set_up = client.post("/api/connections/oauth_fake/oauth/start", json={})
        save(client, "fake", account="me")
        not_oauth = client.post("/api/connections/fake/oauth/start", json={})
    assert not_set_up.status_code == 409
    assert not_oauth.status_code == 409


def test_oauth_state_single_use(app_factory: AppFactory) -> None:
    plugin = OAuthFakePlugin()
    with signed_in_client(app_factory, plugin) as client:
        state = start_oauth(client)
        first = client.get(CALLBACK, params={"state": state, "code": "c"})
        second = client.get(CALLBACK, params={"state": state, "code": "c"})
    assert first.headers["location"].endswith("?signed_in=1")
    assert second.status_code == 303
    assert sign_in_error(second.headers["location"]) == (
        "This sign-in link expired. Start again."
    )
    assert second.headers["location"].startswith(f"{PUBLIC}/connections?")
    assert len(plugin.completed) == 1


def test_oauth_state_expires_after_10_minutes(
    app_factory: AppFactory, clock: FixedClock
) -> None:
    plugin = OAuthFakePlugin()
    with signed_in_client(app_factory, plugin) as client:
        state = start_oauth(client)
        clock.advance(timedelta(minutes=10, seconds=1))
        response = client.get(CALLBACK, params={"state": state, "code": "c"})
    assert sign_in_error(response.headers["location"]) == (
        "This sign-in link expired. Start again."
    )
    assert plugin.completed == []


def test_oauth_state_valid_just_before_expiry(
    app_factory: AppFactory, clock: FixedClock
) -> None:
    with signed_in_client(app_factory, OAuthFakePlugin()) as client:
        state = start_oauth(client)
        clock.advance(timedelta(minutes=9, seconds=59))
        response = client.get(CALLBACK, params={"state": state, "code": "c"})
    assert response.headers["location"].endswith("?signed_in=1")


def test_oauth_unknown_state_saves_nothing(app_factory: AppFactory) -> None:
    plugin = OAuthFakePlugin()
    with signed_in_client(app_factory, plugin) as client:
        start_oauth(client)
        response = client.get(CALLBACK, params={"state": "forged", "code": "c"})
        missing = client.get(CALLBACK, params={"code": "c"})
        stored = services(client).credentials.get(ServiceId("oauth_fake"))
    assert response.status_code == 303
    assert response.headers["location"].startswith(f"{PUBLIC}/connections?")
    assert missing.headers["location"].startswith(f"{PUBLIC}/connections?")
    assert plugin.completed == []
    assert "refresh_token" not in stored


def test_oauth_error_param_redirects_with_message(app_factory: AppFactory) -> None:
    plugin = OAuthFakePlugin()
    with signed_in_client(app_factory, plugin) as client:
        state = start_oauth(client)
        response = client.get(
            CALLBACK, params={"state": state, "error": "access_denied"}
        )
    location = response.headers["location"]
    assert location.startswith(f"{PUBLIC}/connections/oauth_fake?")
    assert sign_in_error(location) == "OAuth Fake sign-in failed: access_denied"
    assert plugin.completed == []


def test_oauth_missing_code_redirects_with_message(app_factory: AppFactory) -> None:
    with signed_in_client(app_factory, OAuthFakePlugin()) as client:
        state = start_oauth(client)
        response = client.get(CALLBACK, params={"state": state})
    location = response.headers["location"]
    assert location.startswith(f"{PUBLIC}/connections/oauth_fake?")
    assert sign_in_error(location).startswith("OAuth Fake sign-in failed")


def test_oauth_complete_failure_redirects_without_secrets(
    app_factory: AppFactory,
) -> None:
    plugin = OAuthFakePlugin()
    with signed_in_client(app_factory, plugin) as client:
        state = start_oauth(client)
        response = client.get(CALLBACK, params={"state": state, "code": "bad"})
        stored = services(client).credentials.get(ServiceId("oauth_fake"))
    location = response.headers["location"]
    assert location.startswith(f"{PUBLIC}/connections/oauth_fake?")
    message = sign_in_error(location)
    assert "bad code" in message
    assert state not in location
    assert "verifier" not in location
    assert "refresh_token" not in stored


def test_browser_sign_in_round_trip_and_bad_input_422(
    app_factory: AppFactory,
) -> None:
    with signed_in_client(app_factory, BrowserFakePlugin(), FakePlugin()) as client:
        save(client, "browser_fake", team="T1")
        config = client.get("/api/connections/browser_fake/browser-sign-in")
        bad = client.post(
            "/api/connections/browser_fake/browser-sign-in", json={"result": {}}
        )
        good = client.post(
            "/api/connections/browser_fake/browser-sign-in",
            json={"result": {"user_token": "ut"}},
        )
        save(client, "fake", account="me")
        unsupported = client.get("/api/connections/fake/browser-sign-in")
        unsupported_post = client.post(
            "/api/connections/fake/browser-sign-in", json={"result": {}}
        )
        stored = services(client).credentials.get(ServiceId("browser_fake"))
    assert config.status_code == 200
    assert config.json() == {"config": {"app_name": "LinerSync", "team": "T1"}}
    assert bad.status_code == 422
    assert bad.json() == {"detail": "The sign-in returned no user token"}
    assert good.status_code == 200
    assert good.json()["status"] == "untested"
    assert stored["user_token"] == "ut"
    assert unsupported.status_code == 409
    assert unsupported_post.status_code == 409


DEVICE = "/api/connections/device_fake/device-code"


def test_device_code_round_trip(app_factory: AppFactory) -> None:
    plugin = DeviceFakePlugin()
    with signed_in_client(app_factory, plugin) as client:
        save(client, "device_fake", client_id="cid")
        start = client.post(f"{DEVICE}/start", json={})
        pending = client.post(f"{DEVICE}/poll", json={})
        plugin.outcome = "done"
        done = client.post(f"{DEVICE}/poll", json={})
        after = client.post(f"{DEVICE}/poll", json={})
        info = client.get("/api/connections/device_fake").json()
        stored = services(client).credentials.get(ServiceId("device_fake"))
    assert start.status_code == 200
    assert start.json() == {
        "user_code": "ABCD-EFGH",
        "verification_url": "https://device.example/activate",
        "interval_s": 5,
        "expires_in_s": 600,
    }
    assert pending.json() == {"status": "pending", "message": "", "interval_s": None}
    assert done.json() == {"status": "done", "message": "", "interval_s": None}
    assert plugin.polled[0] == {"device_code": "dc1"}
    assert info["status"] == "untested"
    assert stored["refresh_token"] == "r"
    # The flow is over: a later poll has nothing to wait for.
    assert after.json()["status"] == "expired"


def test_device_code_denied(app_factory: AppFactory) -> None:
    plugin = DeviceFakePlugin()
    with signed_in_client(app_factory, plugin) as client:
        save(client, "device_fake", client_id="cid")
        client.post(f"{DEVICE}/start", json={})
        plugin.outcome = "denied"
        denied = client.post(f"{DEVICE}/poll", json={})
        again = client.post(f"{DEVICE}/poll", json={})
    assert denied.json() == {
        "status": "denied",
        "message": "You declined the sign-in",
        "interval_s": None,
    }
    assert again.json()["status"] == "expired"


def test_device_code_expired(app_factory: AppFactory, clock: FixedClock) -> None:
    plugin = DeviceFakePlugin()
    with signed_in_client(app_factory, plugin) as client:
        save(client, "device_fake", client_id="cid")
        client.post(f"{DEVICE}/start", json={})
        clock.advance(timedelta(seconds=601))
        expired = client.post(f"{DEVICE}/poll", json={})
        restart = client.post(f"{DEVICE}/start", json={})
        plugin.outcome = "done"
        done = client.post(f"{DEVICE}/poll", json={})
    assert expired.json() == {
        "status": "expired",
        "message": "This code expired. Get a new one.",
        "interval_s": None,
    }
    assert plugin.polled == [{"device_code": "dc2"}]
    assert restart.status_code == 200
    assert done.json()["status"] == "done"


def test_device_code_poll_without_start(app_factory: AppFactory) -> None:
    with signed_in_client(app_factory, DeviceFakePlugin()) as client:
        save(client, "device_fake", client_id="cid")
        response = client.post(f"{DEVICE}/poll", json={})
    assert response.json() == {
        "status": "expired",
        "message": "This code expired. Get a new one.",
        "interval_s": None,
    }


def test_device_code_second_start_shares_the_pending_code(
    app_factory: AppFactory, clock: FixedClock
) -> None:
    plugin = DeviceFakePlugin()
    with signed_in_client(app_factory, plugin) as client:
        save(client, "device_fake", client_id="cid")
        first = client.post(f"{DEVICE}/start", json={})
        clock.advance(timedelta(seconds=100))
        second = client.post(f"{DEVICE}/start", json={})
        client.post(f"{DEVICE}/poll", json={})
    assert plugin.started == 1
    assert second.json()["user_code"] == first.json()["user_code"]
    assert second.json()["expires_in_s"] == 500
    assert plugin.polled == [{"device_code": "dc1"}]


def test_device_code_survives_a_restart(app_factory: AppFactory) -> None:
    plugin = DeviceFakePlugin()
    with signed_in_client(app_factory, plugin) as client:
        save(client, "device_fake", client_id="cid")
        client.post(f"{DEVICE}/start", json={})
    with signed_in_client(app_factory, plugin) as client:
        plugin.outcome = "done"
        done = client.post(f"{DEVICE}/poll", json={})
        after = client.post(f"{DEVICE}/poll", json={})
    assert done.json()["status"] == "done"
    assert plugin.polled == [{"device_code": "dc1"}]
    assert after.json()["status"] == "expired"


def test_device_code_slow_down_is_kept(app_factory: AppFactory) -> None:
    plugin = DeviceFakePlugin()
    with signed_in_client(app_factory, plugin) as client:
        save(client, "device_fake", client_id="cid")
        client.post(f"{DEVICE}/start", json={})
        plugin.slow_down_to = 10
        slowed = client.post(f"{DEVICE}/poll", json={})
        plugin.slow_down_to = None
        client.post(f"{DEVICE}/poll", json={})
        again = client.post(f"{DEVICE}/start", json={})
    assert slowed.json()["interval_s"] == 10
    assert plugin.polled[1] == {"device_code": "dc1", "interval_s": "10"}
    assert again.json()["interval_s"] == 10


def test_device_code_needs_setup_and_support(app_factory: AppFactory) -> None:
    with signed_in_client(app_factory, DeviceFakePlugin(), FakePlugin()) as client:
        not_set_up = client.post(f"{DEVICE}/start", json={})
        save(client, "fake", account="me")
        unsupported = client.post("/api/connections/fake/device-code/start", json={})
        unsupported_poll = client.post(
            "/api/connections/fake/device-code/poll", json={}
        )
    assert not_set_up.status_code == 409
    assert unsupported.status_code == 409
    assert unsupported.json() == {"detail": "Fake doesn't use a device code"}
    assert unsupported_poll.status_code == 409


def test_auth_required_marks_needs_sign_in(app_factory: AppFactory) -> None:
    with signed_in_client(app_factory, FakePlugin()) as client:
        save(client, "fake", account="me")
        client.post("/api/connections/fake/test", json={})
        manager(client).on_auth_required(ServiceId("fake"))
        info = client.get("/api/connections/fake").json()
    assert info["status"] == "needs_sign_in"
    assert info["account_name"] == "fake"


def test_disconnect_deletes_credentials_keeps_syncs(app_factory: AppFactory) -> None:
    with signed_in_client(app_factory, FakePlugin()) as client:
        syncs = SqliteSyncs(services(client).engine)
        syncs.save(group(source=("fake", "P1")))
        save(client, "fake", account="me")
        client.post("/api/connections/fake/test", json={})
        response = client.request("DELETE", "/api/connections/fake", json={})
        info = client.get("/api/connections/fake").json()
        stored = services(client).credentials.get(ServiceId("fake"))
    assert response.status_code == 204
    assert info["status"] == "not_set_up"
    assert info["account_name"] is None
    assert stored == {}
    assert syncs.get("s1") is not None


def test_undecryptable_credentials_show_not_set_up(app_factory: AppFactory) -> None:
    first_key = Fernet.generate_key().decode()
    with signed_in_client(app_factory, FakePlugin(), secret_key=first_key) as client:
        save(client, "fake", account="me")
        client.post("/api/connections/fake/test", json={})
    other_key = Fernet.generate_key().decode()
    with signed_in_client(app_factory, FakePlugin(), secret_key=other_key) as client:
        response = client.get("/api/connections/fake")
        listing = client.get("/api/connections")
        connectors = manager(client).connectors()
    assert response.status_code == 200
    assert response.json()["status"] == "not_set_up"
    assert listing.status_code == 200
    assert connectors == {}


def test_connectors_only_for_set_up_services(app_factory: AppFactory) -> None:
    fake = FakePlugin()
    with signed_in_client(app_factory, fake, SecretFakePlugin()) as client:
        save(client, "fake", account="me")
        connectors = manager(client).connectors()
    assert connectors == {ServiceId("fake"): fake.connector}


def test_spotify_guide_shows_redirect_uri(app_factory: AppFactory) -> None:
    with signed_in_client(
        app_factory, SPOTIFY_PLUGIN, public_url="https://music.example"
    ) as client:
        guide = client.get("/api/connections/spotify/guide").json()
        info = client.get("/api/connections/spotify").json()
    copies = [cv for step in guide["steps"] for cv in step["copy_values"]]
    assert copies == [
        {
            "label": "Redirect URI",
            "value": "https://music.example/api/connections/oauth/callback",
        }
    ]
    assert info["category"] == "streaming"


def test_resources_only_whitelisted(app_factory: AppFactory) -> None:
    with signed_in_client(app_factory, ScreenshotPlugin(), FakePlugin()) as client:
        base = "/api/connections/shot_fake/resources"
        served = client.get(f"{base}/{SHOT}")
        traversal = client.get(f"{base}/..%2Fplugin.py")
        parent = client.get(f"{base}/../plugins.py")
        other = client.get(f"{base}/resources/other.png")
        not_listed = client.get(f"{base}/plugins.py")
        wrong_service = client.get(f"/api/connections/fake/resources/{SHOT}")
    assert served.status_code == 200
    assert served.content.startswith(b"\x89PNG")
    assert served.headers["content-type"] == "image/png"
    for response in (traversal, parent, other, not_listed, wrong_service):
        assert response.status_code == 404


def test_playlists_listing_and_unreadable(app_factory: AppFactory) -> None:
    plugin = FakePlugin()
    mix = snapshot("fake", "P1", [])
    plugin.connector.add_playlist(replace(mix, meta=replace(mix.meta, track_count=0)))
    locked = snapshot("fake", "P2", [], name="Locked")
    plugin.connector.add_playlist(
        replace(
            locked,
            meta=replace(
                locked.meta,
                owned=False,
                readable=False,
                unreadable_reason="Private playlist",
            ),
        )
    )
    with signed_in_client(app_factory, plugin) as client:
        not_set_up = client.get("/api/connections/fake/playlists")
        save(client, "fake", account="me")
        response = client.get("/api/connections/fake/playlists")
    assert not_set_up.status_code == 409
    assert response.status_code == 200
    by_id = {p["playlist_id"]: p for p in response.json()}
    assert by_id["P1"] == {
        "service": "fake",
        "playlist_id": "P1",
        "name": "Mix",
        "description": "",
        "cover_url": None,
        "owned": True,
        "readable": True,
        "unreadable_reason": None,
        "track_count": 0,
    }
    assert by_id["P2"]["readable"] is False
    assert by_id["P2"]["unreadable_reason"] == "Private playlist"


def test_playlists_auth_error_gives_502_and_needs_sign_in(
    app_factory: AppFactory,
) -> None:
    from linersync.ports.errors import ReadFailed

    plugin = FakePlugin()

    async def fail() -> list[PlaylistMeta]:
        raise ReadFailed(ServiceId("fake"), "Sign in again", auth_required=True)

    plugin.connector.list_playlists = fail  # type: ignore[method-assign]
    with signed_in_client(app_factory, plugin) as client:
        save(client, "fake", account="me")
        response = client.get("/api/connections/fake/playlists")
        info = client.get("/api/connections/fake").json()
    assert response.status_code == 502
    assert response.json() == {"detail": "Sign in again"}
    assert info["status"] == ConnectionStatus.NEEDS_SIGN_IN


@pytest.mark.parametrize("value", ["{redirect_uri}", "{public_url}"])
def test_guide_placeholders(app_factory: AppFactory, value: str) -> None:
    from linersync.ports.setup import CopyValue, SetupGuide, SetupStep, StepKind

    class Placeholder(FakePlugin):
        def setup_guide(self) -> SetupGuide:
            return SetupGuide(
                self.service,
                self.display_name,
                self.category,
                (),
                (
                    SetupStep(
                        "x", "X", StepKind.INFO, copy_values=(CopyValue("v", value),)
                    ),
                    SetupStep("test", "Test", StepKind.TEST),
                ),
            )

    with signed_in_client(app_factory, Placeholder()) as client:
        guide = client.get("/api/connections/fake/guide").json()
    rendered = guide["steps"][0]["copy_values"][0]["value"]
    assert (
        rendered
        == {
            "{redirect_uri}": f"{PUBLIC}{CALLBACK}",
            "{public_url}": PUBLIC,
        }[value]
    )


def test_test_that_raises_marks_failing_without_echoing(
    app_factory: AppFactory,
) -> None:
    class Raising(FakePlugin):
        async def test_connection(self, ctx: Any) -> Any:
            raise KeyError("tok-LEAKED")

    with signed_in_client(app_factory, Raising()) as client:
        save(client, "fake", account="me")
        response = client.post("/api/connections/fake/test", json={})
        info = client.get("/api/connections/fake").json()
    assert response.status_code == 200
    assert "tok-LEAKED" not in response.text
    assert response.json()["ok"] is False
    assert info["status"] == "failing"


class BrokenPlugin(FakePlugin):
    """A plugin whose connector can't be built; its error text looks secret."""

    def __init__(self) -> None:
        super().__init__(
            FakeConnector(ServiceId("broken"), "Broken", frozenset(Capability))
        )

    def create(self, ctx: Any) -> Any:
        raise RuntimeError("tok-LEAKED")


def test_broken_plugin_create_fails_only_its_own_jobs(
    app_factory: AppFactory, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    a = FakeConnector(ServiceId("fake_a"), "Fake A", frozenset(Capability))
    b = FakeConnector(
        ServiceId("fake_b"),
        "Fake B",
        frozenset(Capability),
        catalog=[track("fake_b", t.upper(), title=t) for t in ("a", "b")],
    )
    a.add_playlist(snapshot("fake_a", "P1", ["a", "b"]))
    b.add_playlist(snapshot("fake_b", "B1", []))
    plugins = (FakePlugin(a), FakePlugin(b), BrokenPlugin())
    with signed_in_client(app_factory, *plugins) as client:
        for service in ("fake_a", "fake_b", "broken"):
            assert save(client, service, account="me").status_code == 200
        sync_id = create(client)["sync_id"]
        ok = run_now(client, sync_id).json()
        transfer = client.post(
            "/api/transfers",
            json={
                "source_service": "fake_a",
                "playlist_id": "P1",
                "destination": "broken",
            },
        ).json()
        drain(client)
        ok_done = client.get(f"/api/jobs/{ok['job_id']}").json()
        broken_done = client.get(f"/api/jobs/{transfer['job_id']}").json()
        info = client.get("/api/connections/broken").json()
    assert ok_done["status"] == "succeeded"
    assert [e.track.track_id for e in b.playlists["B1"].entries] == ["A", "B"]
    assert broken_done["status"] == "failed"
    assert "tok-LEAKED" not in (broken_done["error"] or "")
    assert info["status"] == "failing"
    assert info["message"] == "Couldn't start the Broken connector"
    assert "tok-LEAKED" not in caplog.text
    assert "RuntimeError" in caplog.text
