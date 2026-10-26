from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from linersync.adapters.connectors.apple_music.token import (
    TOKEN_LIFETIME,
    DeveloperTokens,
    check_private_key,
    make_developer_token,
)
from linersync.adapters.memory.runtime import FixedClock

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _pem(key: ec.EllipticCurvePrivateKey | rsa.RSAPrivateKey) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


@pytest.fixture
def ec_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def test_token_header_and_claims(ec_key: ec.EllipticCurvePrivateKey) -> None:
    token = make_developer_token("TEAM123456", "KEY1234567", _pem(ec_key), NOW)
    header = jwt.get_unverified_header(token)
    assert header["alg"] == "ES256"
    assert header["kid"] == "KEY1234567"
    claims = jwt.decode(
        token, ec_key.public_key(), algorithms=["ES256"], options={"verify_exp": False}
    )
    assert claims["iss"] == "TEAM123456"
    assert claims["exp"] - claims["iat"] == int(timedelta(days=180).total_seconds())
    assert claims["iat"] == int(NOW.timestamp())
    assert timedelta(days=180) == TOKEN_LIFETIME


def test_token_cached_then_renewed_near_expiry(
    ec_key: ec.EllipticCurvePrivateKey,
) -> None:
    clock = FixedClock(NOW)
    tokens = DeveloperTokens("T", "K", _pem(ec_key), clock)
    first = tokens.current()
    clock.advance(timedelta(days=100))
    assert tokens.current() == first
    clock.advance(timedelta(days=79, seconds=1))
    second = tokens.current()
    assert second != first
    assert tokens.current() == second


def test_force_renew_issues_new_token(ec_key: ec.EllipticCurvePrivateKey) -> None:
    clock = FixedClock(NOW)
    tokens = DeveloperTokens("T", "K", _pem(ec_key), clock)
    first = tokens.current()
    clock.advance(timedelta(seconds=5))
    assert tokens.force_renew() != first


def test_check_private_key_accepts_ec_p256(ec_key: ec.EllipticCurvePrivateKey) -> None:
    assert check_private_key(_pem(ec_key)) is None


def test_check_private_key_rejects_rsa_and_garbage(
    ec_key: ec.EllipticCurvePrivateKey,
) -> None:
    rsa_pem = _pem(rsa.generate_private_key(65537, 2048))
    assert check_private_key(rsa_pem) is not None
    assert check_private_key("not a key") is not None
    p384 = _pem(ec.generate_private_key(ec.SECP384R1()))
    assert check_private_key(p384) is not None
    public = ec_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    assert check_private_key(public.decode()) is not None
