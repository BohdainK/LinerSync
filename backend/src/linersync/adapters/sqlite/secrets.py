"""The Fernet secret key: from the environment, or a 0600 file in the data dir."""

import os
from pathlib import Path

from cryptography.fernet import Fernet

KEY_FILE = "secret.key"


def load_or_create_key(data_dir: Path, env_value: str | None) -> bytes:
    if env_value:
        key = env_value.strip().encode()
        try:
            Fernet(key)
        except (ValueError, TypeError):
            raise ValueError("LINERSYNC_SECRET_KEY is not a valid key") from None
        return key
    path = data_dir / KEY_FILE
    if path.exists():
        return path.read_bytes().strip()
    data_dir.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key)
    return key
