import pytest

from linersync.adapters.connectors.kit.http import HttpFailure
from linersync.adapters.connectors.qobuz.auth import QobuzAuth
from linersync.adapters.connectors.qobuz.parse import SERVICE
from linersync.adapters.memory.credentials import InMemoryCredentials


async def test_headers_carry_app_id_and_token() -> None:
    store = InMemoryCredentials()
    store.save(SERVICE, {"app_id": " 123456789 ", "user_auth_token": "tok\n"})
    auth = QobuzAuth(store)
    assert await auth.headers() == {
        "X-App-Id": "123456789",
        "X-User-Auth-Token": "tok",
    }
    assert await auth.refresh() is False


@pytest.mark.parametrize("missing", ["app_id", "user_auth_token"])
async def test_missing_value_asks_to_sign_in(missing: str) -> None:
    store = InMemoryCredentials()
    values = {"app_id": "123456789", "user_auth_token": "tok"}
    del values[missing]
    store.save(SERVICE, values)
    with pytest.raises(HttpFailure) as e:
        await QobuzAuth(store).headers()
    assert e.value.auth_required is True
    assert e.value.maybe_applied is False
