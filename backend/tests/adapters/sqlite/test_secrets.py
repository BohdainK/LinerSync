from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from linersync.adapters.sqlite.secrets import load_or_create_key


def test_creates_key_file_0600(tmp_path: Path) -> None:
    key = load_or_create_key(tmp_path, None)
    Fernet(key)
    assert (tmp_path / "secret.key").stat().st_mode & 0o777 == 0o600


def test_reuses_existing_key(tmp_path: Path) -> None:
    assert load_or_create_key(tmp_path, None) == load_or_create_key(tmp_path, "")


def test_env_key_wins_and_is_validated(tmp_path: Path) -> None:
    env = Fernet.generate_key().decode()
    assert load_or_create_key(tmp_path, env) == env.encode()
    assert not (tmp_path / "secret.key").exists()
    with pytest.raises(ValueError, match="LINERSYNC_SECRET_KEY is not a valid key"):
        load_or_create_key(tmp_path, "nope")
