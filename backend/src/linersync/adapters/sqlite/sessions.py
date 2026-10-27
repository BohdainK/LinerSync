"""Server-side login sessions. Only the sha256 of a token is stored."""

import hashlib
import secrets
from datetime import datetime, timedelta

import sqlalchemy as sa
from sqlalchemy import Engine

from linersync.adapters.sqlite.db import utc_text
from linersync.adapters.sqlite.schema import sessions


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class SqliteSessions:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def create(self, now: datetime, lifetime: timedelta) -> str:
        token = secrets.token_urlsafe(32)
        with self._engine.begin() as conn:
            conn.execute(
                sa.insert(sessions).values(
                    token_hash=_hash(token),
                    created_at=utc_text(now),
                    expires_at=utc_text(now + lifetime),
                )
            )
        return token

    def valid(self, token: str, now: datetime) -> bool:
        key = _hash(token)
        with self._engine.begin() as conn:
            expires = conn.execute(
                sa.select(sessions.c.expires_at).where(sessions.c.token_hash == key)
            ).scalar_one_or_none()
            if expires is None:
                return False
            if expires > utc_text(now):
                return True
            conn.execute(sa.delete(sessions).where(sessions.c.token_hash == key))
            return False

    def delete(self, token: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                sa.delete(sessions).where(sessions.c.token_hash == _hash(token))
            )
