"""App settings: default schedule and the engine's brake, match and breaker."""

from dataclasses import replace
from datetime import timedelta

from fastapi import APIRouter, Depends

from linersync.application.config import BreakerConfig
from linersync.application.settings import AppSettings
from linersync.domain.planning.brake import BrakeConfig
from linersync.server.api.common import Services, check_schedule, reconcile_schedule
from linersync.server.api.schemas import SettingsIn, SettingsOut
from linersync.server.auth import require_session
from linersync.server.state import AppServices

router = APIRouter(prefix="/api/settings", dependencies=[Depends(require_session)])


def _out(services: AppServices, s: AppSettings) -> SettingsOut:
    engine = s.engine
    return SettingsOut(
        default_schedule=s.default_schedule,
        timezone=services.config.timezone,
        brake_max_fraction=engine.brake.max_fraction,
        brake_max_count=engine.brake.max_count,
        match_accept_threshold=engine.match.accept_threshold,
        match_min_margin=engine.match.min_margin,
        breaker_failure_threshold=engine.breaker.failure_threshold,
        breaker_cooldown_minutes=int(engine.breaker.cooldown / timedelta(minutes=1)),
        backup_schedule=s.backup_schedule,
        backup_versions_kept=s.backup_versions_kept,
    )


@router.get("")
async def get_settings(services: Services) -> SettingsOut:
    return _out(services, services.settings_store.get())


@router.put("")
async def put_settings(body: SettingsIn, services: Services) -> SettingsOut:
    check_schedule(services, body.default_schedule)
    check_schedule(services, body.backup_schedule)
    current = services.settings_store.get()
    engine = current.engine
    updated = replace(
        current,
        default_schedule=body.default_schedule,
        backup_schedule=body.backup_schedule,
        backup_versions_kept=body.backup_versions_kept,
        engine=replace(
            engine,
            match=replace(
                engine.match,
                accept_threshold=body.match_accept_threshold,
                min_margin=body.match_min_margin,
            ),
            brake=BrakeConfig(body.brake_max_fraction, body.brake_max_count),
            breaker=BreakerConfig(
                body.breaker_failure_threshold,
                timedelta(minutes=body.breaker_cooldown_minutes),
            ),
        ),
    )
    services.settings_store.save(updated)
    reconcile_schedule(services)
    return _out(services, updated)
