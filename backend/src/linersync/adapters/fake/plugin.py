from collections.abc import Mapping
from datetime import datetime

from linersync.adapters.connectors.kit.plugin import (
    Category,
    ConnectionTest,
    ConnectorContext,
)
from linersync.adapters.fake.connector import FakeConnector
from linersync.domain.capabilities import Capability
from linersync.domain.tracks import ServiceId
from linersync.ports.connector import Connector
from linersync.ports.quota import QuotaPolicy
from linersync.ports.setup import (
    SetupField,
    SetupGuide,
    SetupStep,
    StepKind,
    validate_values,
)


class FakePlugin:
    """A ConnectorPlugin around a FakeConnector, for dev and tests."""

    category: Category = "self_hosted"

    def __init__(self, connector: FakeConnector | None = None) -> None:
        self.connector = connector or FakeConnector(
            ServiceId("fake"), "Fake", frozenset(Capability)
        )
        self.service = self.connector.service
        self.display_name = self.connector.display_name
        self.capabilities = self.connector.capabilities
        self.limits = self.connector.limits
        self.quota_policy = QuotaPolicy()

    def setup_guide(self) -> SetupGuide:
        return SetupGuide(
            self.service,
            self.display_name,
            self.category,
            (),
            (
                SetupStep(
                    "account",
                    "Account",
                    StepKind.FORM,
                    fields=(SetupField("account", "Account"),),
                ),
                SetupStep("test", "Test the connection", StepKind.TEST),
            ),
        )

    def validate(self, values: Mapping[str, str]) -> dict[str, str]:
        return validate_values(self.setup_guide(), values)

    async def test_connection(self, ctx: ConnectorContext) -> ConnectionTest:
        return ConnectionTest(True, "Connected", account_name="fake")

    def login_expires_at(self, credentials: Mapping[str, str]) -> datetime | None:
        return None

    def create(self, ctx: ConnectorContext) -> Connector:
        return self.connector
