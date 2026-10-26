from collections.abc import Callable

import pytest

from linersync.adapters.memory.credentials import InMemoryCredentials
from linersync.domain.tracks import ServiceId
from linersync.ports.credentials import CredentialStore

Factory = Callable[[], CredentialStore]
pytestmark = pytest.mark.parametrize("make", [InMemoryCredentials])
SP = ServiceId("spotify")


def test_roundtrip(make: Factory) -> None:
    s = make()
    s.save(SP, {"a": "1", "b": "2"})
    assert s.get(SP) == {"a": "1", "b": "2"}


def test_save_merges(make: Factory) -> None:
    s = make()
    s.save(SP, {"a": "1", "b": "2"})
    s.save(SP, {"b": "3", "c": "4"})
    assert s.get(SP) == {"a": "1", "b": "3", "c": "4"}


def test_unknown_is_empty(make: Factory) -> None:
    assert make().get(SP) == {}


def test_delete(make: Factory) -> None:
    s = make()
    s.save(SP, {"a": "1"})
    s.save(ServiceId("apple_music"), {"x": "y"})
    s.delete(SP)
    s.delete(SP)
    assert s.get(SP) == {}
    assert s.get(ServiceId("apple_music")) == {"x": "y"}


def test_get_returns_copy(make: Factory) -> None:
    s = make()
    s.save(SP, {"a": "1"})
    s.get(SP)["a"] = "changed"
    assert s.get(SP) == {"a": "1"}
