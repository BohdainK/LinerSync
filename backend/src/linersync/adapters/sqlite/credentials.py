"""Credentials encrypted at rest with Fernet (plan 3 D6)."""

import json
import logging
from collections.abc import Mapping
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Engine, Select, delete, select
from sqlalchemy.dialects.sqlite import insert

from linersync.adapters.sqlite.schema import credentials
from linersync.domain.tracks import ServiceId

logger = logging.getLogger(__name__)


class SqliteCredentials:
    def __init__(self, engine: Engine, key: bytes) -> None:
        self._engine = engine
        self._fernet = Fernet(key)

    def get(self, service: ServiceId) -> dict[str, str]:
        with self._engine.connect() as conn:
            return self._read(
                conn.execute(self._select(service)).scalar_one_or_none(), service
            )

    def save(self, service: ServiceId, values: Mapping[str, str]) -> None:
        with self._engine.begin() as conn:
            current = self._read(
                conn.execute(self._select(service)).scalar_one_or_none(), service
            )
            current.update(values)
            blob = self._fernet.encrypt(json.dumps(current).encode())
            stmt = insert(credentials).values(service=service, ciphertext=blob)
            conn.execute(
                stmt.on_conflict_do_update(
                    index_elements=["service"], set_={"ciphertext": blob}
                )
            )

    def delete(self, service: ServiceId) -> None:
        with self._engine.begin() as conn:
            conn.execute(delete(credentials).where(credentials.c.service == service))

    @staticmethod
    def _select(service: ServiceId) -> Select[Any]:
        return select(credentials.c.ciphertext).where(credentials.c.service == service)

    def _read(self, blob: bytes | None, service: ServiceId) -> dict[str, str]:
        if blob is None:
            return {}
        try:
            data = json.loads(self._fernet.decrypt(blob))
        except InvalidToken:
            logger.warning(
                "credentials for %s can't be decrypted; the secret key changed?",
                service,
            )
            return {}
        return {str(k): str(v) for k, v in data.items()}
