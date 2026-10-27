from dataclasses import replace
from datetime import timedelta

from linersync.adapters.sqlite.db import connect, migrate
from linersync.adapters.sqlite.settings import SqliteSettings
from linersync.application.config import BreakerConfig, EngineConfig
from linersync.application.settings import DEFAULT_SCHEDULE, AppSettings
from linersync.domain.planning.brake import BrakeConfig


def _store() -> SqliteSettings:
    engine = connect(":memory:")
    migrate(engine)
    return SqliteSettings(engine)


def test_defaults_when_empty() -> None:
    s = _store().get()
    assert s == AppSettings()
    assert s.default_schedule == DEFAULT_SCHEDULE == "0 */6 * * *"


def test_roundtrip_custom_engine_config() -> None:
    store = _store()
    custom = AppSettings(
        default_schedule="0 3 * * *",
        engine=replace(
            EngineConfig(),
            brake=BrakeConfig(max_fraction=0.1),
            breaker=BreakerConfig(cooldown=timedelta(minutes=5)),
        ),
    )
    store.save(custom)
    assert store.get() == custom
    store.save(AppSettings())
    assert store.get() == AppSettings()
