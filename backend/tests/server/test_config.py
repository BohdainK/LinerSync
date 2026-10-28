import os
from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet

from linersync.server.config import ConfigError, ServerConfig, check_data_dir


def test_from_env_requires_password() -> None:
    with pytest.raises(ConfigError, match="LINERSYNC_ADMIN_PASSWORD"):
        ServerConfig.from_env({})


def test_from_env_reads_all() -> None:
    config = ServerConfig.from_env(
        {
            "LINERSYNC_ADMIN_PASSWORD": "pw",
            "LINERSYNC_DATA_DIR": "/srv/ls",
            "LINERSYNC_PUBLIC_URL": "https://sync.example.com",
            "LINERSYNC_SECRET_KEY": "k",
            "LINERSYNC_TIMEZONE": "Europe/Amsterdam",
            "LINERSYNC_HOST": "0.0.0.0",
            "LINERSYNC_PORT": "9000",
        }
    )
    assert config == ServerConfig(
        admin_password="pw",
        data_dir=Path("/srv/ls"),
        public_url="https://sync.example.com",
        secret_key="k",
        timezone="Europe/Amsterdam",
        host="0.0.0.0",
        port=9000,
    )
    assert config.connector_settings.public_base_url == "https://sync.example.com"


def test_defaults() -> None:
    config = ServerConfig.from_env({"LINERSYNC_ADMIN_PASSWORD": "pw"})
    assert config.port == 8000
    assert config.secret_key is None
    assert config.public_url == "http://127.0.0.1:8000"


@pytest.mark.parametrize(
    ("var", "value"),
    [
        ("LINERSYNC_PUBLIC_URL", "ftp://x.example"),
        ("LINERSYNC_PUBLIC_URL", "https://x.example/app"),
        ("LINERSYNC_PUBLIC_URL", "not a url"),
        ("LINERSYNC_TIMEZONE", "Mars/Olympus"),
        ("LINERSYNC_PORT", "eighty"),
    ],
)
def test_bad_public_url_and_timezone_rejected(var: str, value: str) -> None:
    with pytest.raises(ConfigError, match=var):
        ServerConfig.from_env({"LINERSYNC_ADMIN_PASSWORD": "pw", var: value})


def test_trailing_slash_url_allowed() -> None:
    config = ServerConfig(admin_password="pw", public_url="https://a.example/")
    assert config.public_origin == "https://a.example"


def test_public_origin_and_secure_cookies() -> None:
    http = ServerConfig(admin_password="pw", public_url="http://host:8080")
    assert http.public_origin == "http://host:8080"
    assert not http.secure_cookies
    https = ServerConfig(admin_password="pw", public_url="https://sync.example.com")
    assert https.public_origin == "https://sync.example.com"
    assert https.secure_cookies


def test_repr_hides_secrets(app_factory: Any) -> None:
    config = ServerConfig(
        admin_password="hunter2-distinct", secret_key="fernet-distinct"
    )
    assert "hunter2-distinct" not in repr(config)
    assert "fernet-distinct" not in repr(config)
    key = Fernet.generate_key().decode()
    app = app_factory(admin_password="hunter2-distinct", secret_key=key)
    text = repr(app.state.services)
    assert "hunter2-distinct" not in text
    assert key not in text


def test_web_dir_from_env(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<html></html>")
    config = ServerConfig.from_env(
        {"LINERSYNC_ADMIN_PASSWORD": "pw", "LINERSYNC_WEB_DIR": str(tmp_path)}
    )
    assert config.web_dir == Path(tmp_path)


def test_web_dir_without_index_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="^LINERSYNC_WEB_DIR has no index.html"):
        ServerConfig.from_env(
            {"LINERSYNC_ADMIN_PASSWORD": "pw", "LINERSYNC_WEB_DIR": str(tmp_path)}
        )


def test_check_data_dir_creates_folder(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b"
    check_data_dir(target)
    assert target.is_dir()
    assert not (target / ".write-test").exists()


@pytest.mark.skipif(os.getuid() == 0, reason="root ignores folder modes")
def test_check_data_dir_unwritable(tmp_path: Path) -> None:
    target = tmp_path / "ro"
    target.mkdir(mode=0o500)
    try:
        with pytest.raises(ConfigError) as info:
            check_data_dir(target)
    finally:
        target.chmod(0o700)
    assert str(target) in str(info.value)
    assert "can't write to the data folder" in str(info.value)
