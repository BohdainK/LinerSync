import logging

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from linersync.adapters.sqlite.credentials import SqliteCredentials
from linersync.adapters.sqlite.db import connect, migrate
from linersync.adapters.sqlite.schema import credentials
from linersync.domain.tracks import ServiceId

SP = ServiceId("spotify")


def test_values_encrypted_at_rest() -> None:
    e = connect(":memory:")
    migrate(e)
    SqliteCredentials(e, Fernet.generate_key()).save(
        SP, {"client_secret": "s3cr3t-value"}
    )
    with e.connect() as conn:
        blob = conn.execute(select(credentials.c.ciphertext)).scalar_one()
    assert b"s3cr3t-value" not in blob


def test_wrong_key_reads_as_empty(caplog: pytest.LogCaptureFixture) -> None:
    e = connect(":memory:")
    migrate(e)
    SqliteCredentials(e, Fernet.generate_key()).save(
        SP, {"client_secret": "s3cr3t-value"}
    )
    other = SqliteCredentials(e, Fernet.generate_key())
    with caplog.at_level(logging.WARNING):
        assert other.get(SP) == {}
    assert "can't be decrypted" in caplog.text
    assert "s3cr3t-value" not in caplog.text
