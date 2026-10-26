from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from linersync.domain.runs import RunEvent

IdFactory = Callable[[], str]


class Clock(Protocol):
    def now(self) -> datetime: ...


class EventPublisher(Protocol):
    def publish(self, event: RunEvent) -> None: ...
