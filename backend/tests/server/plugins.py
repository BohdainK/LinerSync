"""Connector plugins for the server tests."""

from collections.abc import Mapping
from dataclasses import dataclass

from linersync.adapters.connectors.kit.discovery import PluginRegistry
from linersync.adapters.connectors.kit.plugin import (
    AuthorizeRequest,
    ConnectorContext,
    ConnectorPlugin,
    DeviceCodeStart,
    DevicePoll,
    DevicePollStatus,
)
from linersync.adapters.fake.connector import FakeConnector
from linersync.adapters.fake.plugin import FakePlugin
from linersync.domain.capabilities import Capability
from linersync.domain.tracks import ServiceId
from linersync.ports.errors import ReadFailed
from linersync.ports.setup import (
    FieldKind,
    Screenshot,
    SetupField,
    SetupGuide,
    SetupStep,
    StepKind,
)

TEST_STEP = SetupStep("test", "Test the connection", StepKind.TEST)


def registry(*plugins: ConnectorPlugin) -> PluginRegistry:
    return PluginRegistry({p.service: p for p in plugins}, ())


def _connector(service: str, display_name: str) -> FakeConnector:
    return FakeConnector(ServiceId(service), display_name, frozenset(Capability))


def _guide(
    plugin: FakePlugin, *steps: SetupStep, prerequisites: tuple[str, ...] = ()
) -> SetupGuide:
    return SetupGuide(
        plugin.service,
        plugin.display_name,
        plugin.category,
        prerequisites,
        (*steps, TEST_STEP),
    )


class SecretFakePlugin(FakePlugin):
    def __init__(self) -> None:
        super().__init__(_connector("secret_fake", "Secret Fake"))

    def setup_guide(self) -> SetupGuide:
        return _guide(
            self,
            SetupStep(
                "details",
                "Details",
                StepKind.FORM,
                fields=(
                    SetupField("account", "Account"),
                    SetupField(
                        "token",
                        "Token",
                        kind=FieldKind.SECRET,
                        pattern=r"tok-\w+",
                        pattern_hint="Starts with tok-",
                    ),
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class CompleteCall:
    redirect_uri: str
    code: str
    keep: dict[str, str]


class OAuthFakePlugin(FakePlugin):
    def __init__(self) -> None:
        super().__init__(_connector("oauth_fake", "OAuth Fake"))
        self.completed: list[CompleteCall] = []

    def setup_guide(self) -> SetupGuide:
        return _guide(
            self,
            SetupStep(
                "client",
                "Client",
                StepKind.FORM,
                fields=(SetupField("client_id", "Client ID"),),
            ),
            SetupStep("sign_in", "Sign in", StepKind.OAUTH),
        )

    def authorize(
        self, credentials: Mapping[str, str], redirect_uri: str, state: str
    ) -> AuthorizeRequest:
        return AuthorizeRequest(
            f"https://auth.example/?state={state}", {"verifier": "v"}
        )

    async def complete(
        self,
        ctx: ConnectorContext,
        redirect_uri: str,
        code: str,
        keep: Mapping[str, str],
    ) -> None:
        self.completed.append(CompleteCall(redirect_uri, code, dict(keep)))
        if code == "bad":
            raise ReadFailed(self.service, "bad code", auth_required=True)
        ctx.credentials.save(self.service, {"refresh_token": "r"})


class DeviceFakePlugin(FakePlugin):
    """Device-code sign-in; `outcome` is what the next poll reports."""

    def __init__(self) -> None:
        super().__init__(_connector("device_fake", "Device Fake"))
        self.outcome: DevicePollStatus = "pending"
        self.slow_down_to: int | None = None
        self.started = 0
        self.polled: list[dict[str, str]] = []

    def setup_guide(self) -> SetupGuide:
        return _guide(
            self,
            SetupStep(
                "client",
                "Client",
                StepKind.FORM,
                fields=(SetupField("client_id", "Client ID"),),
            ),
            SetupStep("sign_in", "Sign in", StepKind.DEVICE_CODE),
        )

    async def start_device_code(self, ctx: ConnectorContext) -> DeviceCodeStart:
        self.started += 1
        return DeviceCodeStart(
            "ABCD-EFGH",
            "https://device.example/activate",
            5,
            600,
            {"device_code": f"dc{self.started}"},
        )

    async def poll_device_code(
        self, ctx: ConnectorContext, keep: Mapping[str, str]
    ) -> DevicePoll:
        self.polled.append(dict(keep))
        if self.outcome == "done":
            ctx.credentials.save(self.service, {"refresh_token": "r"})
            return DevicePoll("done")
        if self.outcome == "denied":
            return DevicePoll("denied", "You declined the sign-in")
        return DevicePoll(self.outcome, interval_s=self.slow_down_to)


class BrowserFakePlugin(FakePlugin):
    def __init__(self) -> None:
        super().__init__(_connector("browser_fake", "Browser Fake"))

    def setup_guide(self) -> SetupGuide:
        return _guide(
            self,
            SetupStep(
                "team",
                "Team",
                StepKind.FORM,
                fields=(SetupField("team", "Team"),),
            ),
            SetupStep("sign_in", "Sign in", StepKind.BROWSER_SIGN_IN),
        )

    def sign_in_config(self, credentials: Mapping[str, str]) -> dict[str, str]:
        return {"app_name": "LinerSync", "team": credentials["team"]}

    def accept_sign_in(self, ctx: ConnectorContext, result: Mapping[str, str]) -> None:
        token = (result.get("user_token") or "").strip()
        if not token:
            raise ValueError("The sign-in returned no user token")
        ctx.credentials.save(self.service, {"user_token": token})


SHOT = "resources/shot.png"


class ScreenshotPlugin(FakePlugin):
    def __init__(self) -> None:
        super().__init__(_connector("shot_fake", "Shot Fake"))

    def setup_guide(self) -> SetupGuide:
        return _guide(
            self,
            SetupStep(
                "look",
                "Look here",
                StepKind.INFO,
                screenshots=(Screenshot(SHOT, "Where to click"),),
            ),
        )
