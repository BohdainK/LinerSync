from dataclasses import replace

from linersync.application.config import BreakerConfig
from linersync.domain.tracks import ServiceId
from linersync.ports.repositories import BreakerRepository, BreakerState
from linersync.ports.runtime import Clock


class CircuitBreaker:
    """Per-service breaker. Open after `failure_threshold` consecutive failures;
    once `cooldown` has passed it is half-open and allows an attempt."""

    def __init__(
        self, repo: BreakerRepository, clock: Clock, cfg: BreakerConfig
    ) -> None:
        self._repo = repo
        self._clock = clock
        self._cfg = cfg

    def allow(self, service: ServiceId) -> bool:
        state = self._repo.get(service)
        if state.opened_at is None:
            return True
        return self._clock.now() - state.opened_at >= self._cfg.cooldown

    def record_success(self, service: ServiceId) -> bool:
        """True when this success closed an open (or tripped) breaker."""
        state = self._repo.get(service)
        self._repo.save(BreakerState(service))
        return (
            state.opened_at is not None
            or state.consecutive_failures >= self._cfg.failure_threshold
        )

    def record_failure(self, service: ServiceId, error: str) -> bool:
        """True when this failure opened (or re-opened) the breaker."""
        state = self._repo.get(service)
        now = self._clock.now()
        failures = state.consecutive_failures + 1
        if state.opened_at is not None:
            reopened = now - state.opened_at >= self._cfg.cooldown
            state = replace(
                state,
                consecutive_failures=failures,
                opened_at=now if reopened else state.opened_at,
                last_error=error,
            )
            self._repo.save(state)
            return reopened
        opens = failures >= self._cfg.failure_threshold
        self._repo.save(BreakerState(service, failures, now if opens else None, error))
        return opens
