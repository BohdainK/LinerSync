from urllib.parse import parse_qs, urlsplit

import pytest

from linersync.adapters.connectors.tidal.auth import (
    AUTHORIZE_URL,
    SCOPES,
    authorize_url,
    check_redirect_uri,
)


def test_scopes_are_exactly_the_spec() -> None:
    assert " ".join(SCOPES) == (
        "playlists.read playlists.write search.read user.read "
        "collection.read collection.write"
    )


def test_authorize_url() -> None:
    url = authorize_url("client", "https://ls.example/cb", "st", "chal")
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == AUTHORIZE_URL
    assert AUTHORIZE_URL == "https://login.tidal.com/authorize"
    q = parse_qs(parts.query)
    assert q["client_id"] == ["client"]
    assert q["response_type"] == ["code"]
    assert q["redirect_uri"] == ["https://ls.example/cb"]
    assert q["scope"] == [" ".join(SCOPES)]
    assert q["state"] == ["st"]
    assert q["code_challenge_method"] == ["S256"]
    assert q["code_challenge"] == ["chal"]


@pytest.mark.parametrize(
    "uri",
    [
        "https://ls.example/api/connections/oauth/callback",
        "http://127.0.0.1:8000/cb",
        "http://localhost:8000/cb",
    ],
)
def test_redirect_uri_accepted(uri: str) -> None:
    check_redirect_uri(uri)


@pytest.mark.parametrize("uri", ["http://ls.example/cb", "ftp://x/cb", "nonsense"])
def test_redirect_uri_rejected(uri: str) -> None:
    with pytest.raises(ValueError, match="Tidal needs an https:// address"):
        check_redirect_uri(uri)
