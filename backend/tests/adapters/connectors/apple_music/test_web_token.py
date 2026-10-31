import base64
import json
from datetime import UTC, datetime

from linersync.adapters.connectors.apple_music.web_token import find_token

NOW = datetime(2026, 10, 28, tzinfo=UTC)


def _part(obj: dict[str, object]) -> str:
    raw = json.dumps(obj, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _jwt(header: dict[str, object], exp: datetime) -> str:
    claims = {"iss": "AMPWebPlay", "exp": int(exp.timestamp())}
    return f"{_part(header)}.{_part(claims)}.c2ln"


def test_finds_token_whose_header_starts_with_typ() -> None:
    # The web player's header is {"typ":"JWT","alg":...} (seen live 2026-10-28),
    # so the token starts with eyJ0eXAi, not eyJhbGci.
    token = _jwt(
        {"typ": "JWT", "alg": "ES256", "kid": "WebPlayKid"},
        datetime(2027, 1, 1, tzinfo=UTC),
    )
    assert token.startswith("eyJ0eXAi")
    assert find_token(f'const qa="{token}";', NOW) == token


def test_skips_expired_tokens() -> None:
    old = _jwt({"typ": "JWT", "alg": "ES256"}, datetime(2026, 1, 1, tzinfo=UTC))
    assert find_token(f'x="{old}"', NOW) is None


def test_ignores_other_tokens_in_the_bundle() -> None:
    # The bundle also carries tokens for other Apple apps (seen live: kids
    # LT2ZDZSXNQ and 97DQU9QUD6, same expiry); only the web player's counts.
    later = datetime(2027, 6, 1, tzinfo=UTC)
    other = _jwt({"typ": "JWT", "alg": "ES256", "kid": "LT2ZDZSXNQ"}, later)
    mine = _jwt(
        {"typ": "JWT", "alg": "ES256", "kid": "WebPlayKid"},
        datetime(2027, 1, 1, tzinfo=UTC),
    )
    assert find_token(f'a="{other}";b="{mine}"', NOW) == mine
    assert find_token(f'a="{other}"', NOW) is None
