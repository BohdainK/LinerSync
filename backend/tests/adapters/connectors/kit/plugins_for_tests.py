from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime

from linersync.adapters.connectors.kit.plugin import ConnectionTest, ConnectorContext
from linersync.adapters.fake.connector import FakeConnector
from linersync.adapters.fake.plugin import FakePlugin
from linersync.domain.capabilities import Capability
from linersync.domain.tracks import ServiceId
from linersync.ports.connector import Connector, ConnectorLimits
from linersync.ports.quota import QuotaPolicy
from linersync.ports.setup import SetupGuide, SetupStep, StepKind


class _Base:
    service = ServiceId("alpha")
    display_name = "Alpha"
    category = "streaming"
    capabilities = frozenset(Capability)
    limits = ConnectorLimits(10, 10)
    quota_policy = QuotaPolicy()

    def setup_guide(self) -> SetupGuide:
        return SetupGuide(
            ServiceId(self.service),
            self.display_name,
            "streaming",
            (),
            (SetupStep("test", "Test", StepKind.TEST),),
        )

    def validate(self, values: Mapping[str, str]) -> dict[str, str]:
        return {}

    async def test_connection(self, ctx: ConnectorContext) -> ConnectionTest:
        return ConnectionTest(True, "ok")

    def login_expires_at(self, credentials: Mapping[str, str]) -> datetime | None:
        return None

    def create(self, ctx: ConnectorContext) -> Connector:
        return FakeConnector(
            ServiceId(self.service), self.display_name, self.capabilities
        )


class GoodPlugin(_Base):
    pass


class PluginClass(_Base):
    service = ServiceId("beta")
    display_name = "Beta"


class DuplicateAlpha(_Base):
    display_name = "Alpha again"


class InvalidGuidePlugin(_Base):
    service = ServiceId("gamma")

    def setup_guide(self) -> SetupGuide:
        return replace(super().setup_guide(), steps=())


class MismatchedGuidePlugin(_Base):
    service = ServiceId("delta")

    def setup_guide(self) -> SetupGuide:
        return replace(super().setup_guide(), service=ServiceId("other"))


class BadServicePlugin(_Base):
    service = ServiceId("Bad Id")


NOT_A_PLUGIN = object()
GOOD = GoodPlugin()
CLASS_PLUGIN = PluginClass
DUPLICATE = DuplicateAlpha()
INVALID_GUIDE = InvalidGuidePlugin()
MISMATCHED = MismatchedGuidePlugin()
BAD_SERVICE = BadServicePlugin()
FAKE = FakePlugin()
