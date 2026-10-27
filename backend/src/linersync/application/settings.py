"""App-wide settings."""

from dataclasses import dataclass, field

from linersync.application.config import EngineConfig

DEFAULT_SCHEDULE = "0 */6 * * *"
DEFAULT_BACKUP_SCHEDULE = "0 3 * * *"
DEFAULT_VERSIONS_KEPT = 5


@dataclass(frozen=True, slots=True)
class AppSettings:
    default_schedule: str = DEFAULT_SCHEDULE
    engine: EngineConfig = field(default_factory=EngineConfig)
    backup_schedule: str | None = DEFAULT_BACKUP_SCHEDULE
    backup_versions_kept: int = DEFAULT_VERSIONS_KEPT
