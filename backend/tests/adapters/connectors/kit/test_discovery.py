from datetime import UTC, datetime
from importlib.metadata import EntryPoint

from linersync.adapters.connectors.kit.discovery import (
    ENTRY_POINT_GROUP,
    discover_plugins,
)
from linersync.adapters.connectors.kit.plugin import (
    ConnectorContext,
    ConnectorPlugin,
    ConnectorSettings,
)
from linersync.adapters.fake.plugin import FakePlugin
from linersync.adapters.memory.credentials import InMemoryCredentials
from linersync.adapters.memory.quota import InMemoryQuota
from linersync.adapters.memory.runtime import FixedClock
from linersync.domain.tracks import ServiceId
from linersync.ports.setup import check_guide

MOD = "tests.adapters.connectors.kit.plugins_for_tests"


def ep(name: str, obj: str) -> EntryPoint:
    return EntryPoint(name=name, value=f"{MOD}:{obj}", group=ENTRY_POINT_GROUP)


def test_loads_good_plugin() -> None:
    reg = discover_plugins([ep("alpha", "GOOD")])
    assert reg.errors == ()
    assert isinstance(reg.get(ServiceId("alpha")), ConnectorPlugin)


def test_class_entry_point_instantiated() -> None:
    reg = discover_plugins([ep("beta", "CLASS_PLUGIN")])
    assert reg.get(ServiceId("beta")).display_name == "Beta"


def test_broken_import_is_reported_not_raised() -> None:
    bad = EntryPoint(name="broken", value="no.such.module:X", group=ENTRY_POINT_GROUP)
    reg = discover_plugins([bad, ep("alpha", "GOOD")])
    assert [e.name for e in reg.errors] == ["broken"]
    assert "alpha" in reg.plugins


def test_not_a_plugin_rejected() -> None:
    reg = discover_plugins([ep("nope", "NOT_A_PLUGIN")])
    assert reg.plugins == {}
    assert [e.name for e in reg.errors] == ["nope"]


def test_invalid_guide_rejected() -> None:
    reg = discover_plugins([ep("g", "INVALID_GUIDE")])
    assert reg.plugins == {}
    assert [e.name for e in reg.errors] == ["g"]


def test_mismatched_guide_service_rejected() -> None:
    reg = discover_plugins([ep("m", "MISMATCHED")])
    assert reg.plugins == {}
    assert len(reg.errors) == 1


def test_invalid_service_id_rejected() -> None:
    reg = discover_plugins([ep("b", "BAD_SERVICE")])
    assert reg.plugins == {}
    assert len(reg.errors) == 1


def test_duplicate_service_rejected() -> None:
    reg = discover_plugins([ep("b-dup", "DUPLICATE"), ep("a-first", "GOOD")])
    assert list(reg.plugins) == ["alpha"]
    assert reg.errors[0].name == "b-dup"
    assert reg.errors[0].message == "service 'alpha' is already provided by a-first"


async def test_fake_plugin_creates_its_connector_and_tests_ok() -> None:
    plugin = FakePlugin()
    assert plugin.service == "fake"
    assert check_guide(plugin.setup_guide()) == []
    ctx = ConnectorContext(
        InMemoryCredentials(),
        InMemoryQuota(),
        FixedClock(datetime(2026, 1, 1, tzinfo=UTC)),
    )
    assert plugin.create(ctx) is plugin.connector
    assert (await plugin.test_connection(ctx)).ok


def test_redirect_uri_from_base_url() -> None:
    s = ConnectorSettings(public_base_url="https://x.example/")
    assert s.oauth_redirect_uri == "https://x.example/api/connections/oauth/callback"
