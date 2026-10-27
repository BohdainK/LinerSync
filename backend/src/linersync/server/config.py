"""Server configuration read from the environment."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from linersync.adapters.connectors.kit.plugin import ConnectorSettings


class ConfigError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class ServerConfig:
    admin_password: str = field(repr=False)
    data_dir: Path = Path("data")
    public_url: str = "http://127.0.0.1:8000"
    secret_key: str | None = field(default=None, repr=False)
    timezone: str = "UTC"
    host: str = "127.0.0.1"
    port: int = 8000

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "ServerConfig":
        password = env.get("LINERSYNC_ADMIN_PASSWORD", "")
        if not password:
            raise ConfigError("LINERSYNC_ADMIN_PASSWORD is required")
        defaults = cls(admin_password=password)
        public_url = env.get("LINERSYNC_PUBLIC_URL", defaults.public_url)
        _check_public_url(public_url)
        timezone = env.get("LINERSYNC_TIMEZONE", defaults.timezone)
        try:
            ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
            raise ConfigError(
                f"LINERSYNC_TIMEZONE is not a known timezone: {timezone}"
            ) from exc
        raw_port = env.get("LINERSYNC_PORT")
        port = defaults.port
        if raw_port is not None:
            try:
                port = int(raw_port)
            except ValueError as exc:
                raise ConfigError(
                    f"LINERSYNC_PORT must be an integer, got {raw_port!r}"
                ) from exc
        return cls(
            admin_password=password,
            data_dir=Path(env.get("LINERSYNC_DATA_DIR", str(defaults.data_dir))),
            public_url=public_url,
            secret_key=env.get("LINERSYNC_SECRET_KEY") or None,
            timezone=timezone,
            host=env.get("LINERSYNC_HOST", defaults.host),
            port=port,
        )

    @property
    def public_origin(self) -> str:
        parts = urlsplit(self.public_url)
        return f"{parts.scheme}://{parts.netloc}"

    @property
    def secure_cookies(self) -> bool:
        return self.public_url.startswith("https://")

    @property
    def connector_settings(self) -> ConnectorSettings:
        return ConnectorSettings(public_base_url=self.public_url)


def _check_public_url(url: str) -> None:
    parts = urlsplit(url)
    if (
        parts.scheme not in ("http", "https")
        or not parts.netloc
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
    ):
        raise ConfigError(
            f"LINERSYNC_PUBLIC_URL must be an http(s) URL without a path, got {url!r}"
        )
