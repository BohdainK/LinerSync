"""Feature flags on the Settings page: listed, switched without a restart,
kept across restarts, and refused when the server locks them."""

from dataclasses import dataclass
from typing import Any

import pytest

from linersync.adapters.connectors.kit.plugin import FeatureFlag, FlagUnavailable
from linersync.adapters.fake.connector import FakeConnector
from linersync.adapters.fake.plugin import FakePlugin
from linersync.domain.capabilities import Capability
from linersync.domain.tracks import ServiceId
from tests.server.conftest import AppFactory, services
from tests.server.helpers import create, world

ADD_ONLY = frozenset({Capability.READ_ORDER, Capability.ADD})
FULL = frozenset(Capability)
FLAG = "fake_b.editing"


@dataclass
class Setup:
    locked_by: str = ""
    unavailable: str = ""


class FlaggedPlugin(FakePlugin):
    """Fake B can remove and reorder only with its "editing" flag on."""

    setup = Setup()

    def __init__(self, connector: FakeConnector) -> None:
        super().__init__(connector)
        self.flagged = connector.service == "fake_b"
        self.editing = False
        if self.flagged:
            self._apply()

    def _apply(self) -> None:
        caps = FULL if self.editing else ADD_ONLY
        self.capabilities = caps
        self.connector.capabilities = caps

    def feature_flags(self) -> tuple[FeatureFlag, ...]:
        if not self.flagged:
            return ()
        return (
            FeatureFlag(
                "editing",
                "Editing",
                "Lets Fake B remove songs.",
                on=self.editing,
                confirm="Syncs will start removing songs.",
                unavailable=self.setup.unavailable,
                locked_by=self.setup.locked_by,
                affects_full_syncs=True,
            ),
        )

    def set_flag(self, key: str, on: bool) -> None:
        if key != "editing" or not self.flagged:
            raise KeyError(key)
        if self.setup.locked_by and on != self.editing:
            raise FlagUnavailable("locked")
        self.editing = on
        self._apply()


@pytest.fixture(autouse=True)
def _reset_setup() -> None:
    FlaggedPlugin.setup = Setup()


def _dest(client: Any, sync_id: str) -> dict[str, Any]:
    body: dict[str, Any] = client.get(f"/api/syncs/{sync_id}").json()
    dest: dict[str, Any] = body["destinations"][0]
    return dest


def test_lists_the_flag_with_the_syncs_it_affects(app_factory: AppFactory) -> None:
    with world(app_factory, make_plugin=FlaggedPlugin) as w:
        full = create(w.client, name="Full")["sync_id"]
        create(
            w.client,
            name="Adds",
            destinations=[
                {"service": "fake_b", "playlist_id": "B1", "mode": "add_only"}
            ],
        )
        flags = w.client.get("/api/settings/flags").json()
    assert flags == [
        {
            "flag_id": FLAG,
            "service": "fake_b",
            "service_name": "Fake B",
            "title": "Editing",
            "description": "Lets Fake B remove songs.",
            "on": False,
            "confirm": "Syncs will start removing songs.",
            "unavailable": "",
            "locked_by": "",
            "affected_syncs": [{"sync_id": full, "name": "Full"}],
        }
    ]


def test_turning_it_on_takes_effect_without_a_restart(
    app_factory: AppFactory,
) -> None:
    with world(app_factory, make_plugin=FlaggedPlugin) as w:
        sync_id = create(w.client)["sync_id"]
        assert _dest(w.client, sync_id)["effective_mode"] == "add_only"
        on = w.client.put(f"/api/settings/flags/{FLAG}", json={"on": True})
        assert on.status_code == 200, on.text
        assert on.json()["on"] is True
        dest = _dest(w.client, sync_id)
        assert dest["effective_mode"] == "full"
        assert dest["limitations"] == []
        connections = w.client.get("/api/connections").json()["services"]
        b = next(c for c in connections if c["service"] == "fake_b")
        assert "remove" in b["capabilities"]
        connector = services(w.client).connections.connectors()[ServiceId("fake_b")]
        assert Capability.REMOVE in connector.capabilities
        off = w.client.put(f"/api/settings/flags/{FLAG}", json={"on": False})
        assert off.json()["on"] is False
        assert _dest(w.client, sync_id)["effective_mode"] == "add_only"


def test_the_setting_survives_a_restart(app_factory: AppFactory) -> None:
    with world(app_factory, make_plugin=FlaggedPlugin) as w:
        w.client.put(f"/api/settings/flags/{FLAG}", json={"on": True})
        assert services(w.client).settings_store.get().flags == {FLAG: True}
    # A new app over the same data dir, with fresh (off) plugins.
    with world(app_factory, make_plugin=FlaggedPlugin) as w:
        assert w.client.get("/api/settings/flags").json()[0]["on"] is True


def test_other_settings_keep_the_flags(app_factory: AppFactory) -> None:
    with world(app_factory, make_plugin=FlaggedPlugin) as w:
        w.client.put(f"/api/settings/flags/{FLAG}", json={"on": True})
        body = w.client.get("/api/settings").json()
        del body["timezone"]
        assert w.client.put("/api/settings", json=body).status_code == 200
        assert services(w.client).settings_store.get().flags == {FLAG: True}


def test_a_locked_flag_is_refused(app_factory: AppFactory) -> None:
    FlaggedPlugin.setup = Setup(locked_by="FAKE_EDITING")
    with world(app_factory, make_plugin=FlaggedPlugin) as w:
        assert w.client.get("/api/settings/flags").json()[0]["locked_by"] == (
            "FAKE_EDITING"
        )
        response = w.client.put(f"/api/settings/flags/{FLAG}", json={"on": True})
        assert response.status_code == 409
        assert response.json() == {"detail": "FAKE_EDITING sets this on the server"}
        assert services(w.client).settings_store.get().flags == {}


def test_an_unavailable_flag_cant_be_turned_on(app_factory: AppFactory) -> None:
    FlaggedPlugin.setup = Setup(unavailable="Needs the other sign-in")
    with world(app_factory, make_plugin=FlaggedPlugin) as w:
        response = w.client.put(f"/api/settings/flags/{FLAG}", json={"on": True})
        assert response.status_code == 409
        assert response.json() == {"detail": "Needs the other sign-in"}
        assert (
            w.client.put(f"/api/settings/flags/{FLAG}", json={"on": False}).status_code
            == 200
        )


def test_a_stored_flag_that_no_longer_applies_stays_off(
    app_factory: AppFactory,
) -> None:
    with world(app_factory, make_plugin=FlaggedPlugin) as w:
        w.client.put(f"/api/settings/flags/{FLAG}", json={"on": True})
    FlaggedPlugin.setup = Setup(unavailable="Needs the other sign-in")
    with world(app_factory, make_plugin=FlaggedPlugin) as w:
        assert w.client.get("/api/settings/flags").json()[0]["on"] is False


def test_unknown_flag(app_factory: AppFactory) -> None:
    with world(app_factory, make_plugin=FlaggedPlugin) as w:
        response = w.client.put("/api/settings/flags/nope.x", json={"on": True})
    assert response.status_code == 404
    assert response.json() == {"detail": "Unknown feature flag"}


def test_flags_need_a_session(client: Any) -> None:
    assert client.get("/api/settings/flags").status_code == 401
