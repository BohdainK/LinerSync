from dataclasses import dataclass, field
from datetime import timedelta

from linersync.domain.matching.types import MatchConfig
from linersync.domain.planning.brake import BrakeConfig


@dataclass(frozen=True, slots=True)
class BreakerConfig:
    failure_threshold: int = 5
    cooldown: timedelta = timedelta(minutes=30)


@dataclass(frozen=True, slots=True)
class EngineConfig:
    match: MatchConfig = field(default_factory=MatchConfig)
    brake: BrakeConfig = field(default_factory=BrakeConfig)
    breaker: BreakerConfig = field(default_factory=BreakerConfig)
    max_replans: int = 1
