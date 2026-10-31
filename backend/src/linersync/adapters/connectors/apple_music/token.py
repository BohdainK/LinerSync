from datetime import datetime, timedelta

import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from linersync.ports.runtime import Clock

TOKEN_LIFETIME = timedelta(days=180)
RENEW_BEFORE = timedelta(days=1)


def make_developer_token(
    team_id: str, key_id: str, private_key_pem: str, now: datetime
) -> str:
    iat = int(now.timestamp())
    claims = {
        "iss": team_id,
        "iat": iat,
        "exp": iat + int(TOKEN_LIFETIME.total_seconds()),
    }
    return jwt.encode(
        claims, private_key_pem, algorithm="ES256", headers={"kid": key_id}
    )


class DeveloperTokens:
    def __init__(
        self, team_id: str, key_id: str, private_key_pem: str, clock: Clock
    ) -> None:
        self._team_id = team_id
        self._key_id = key_id
        self._pem = private_key_pem
        self._clock = clock
        self._token: str | None = None
        self._expires: datetime | None = None

    def current(self) -> str:
        now = self._clock.now()
        if (
            self._token is None
            or self._expires is None
            or self._expires - now < RENEW_BEFORE
        ):
            return self.force_renew()
        return self._token

    def force_renew(self) -> str:
        now = self._clock.now()
        self._token = make_developer_token(self._team_id, self._key_id, self._pem, now)
        self._expires = now + TOKEN_LIFETIME
        return self._token


def check_private_key(pem: str) -> str | None:
    try:
        key = load_pem_private_key(pem.encode(), password=None)
    except (ValueError, TypeError):
        return "The private key is not a readable unencrypted PEM private key."
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(
        key.curve, ec.SECP256R1
    ):
        return "The private key must be an EC P-256 key (the .p8 file from Apple)."
    return None
