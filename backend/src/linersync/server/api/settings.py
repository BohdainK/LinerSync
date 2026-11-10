"""App settings: default schedule, the engine's brake, match and breaker, and
the plugins' feature flags."""

from dataclasses import replace
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException

from linersync.adapters.connectors.kit.plugin import FlagUnavailable
from linersync.application.config import BreakerConfig
from linersync.application.settings import AppSettings
from linersync.domain.planning.brake import BrakeConfig
from linersync.domain.sync import MemberRole, SyncMode
from linersync.server.api.common import (
    Services,
    check_schedule,
    reconcile_schedule,
)
from linersync.server.api.schemas import (
    FlagIn,
    FlagOut,
    FlagSyncOut,
    SettingsIn,
    SettingsOut,
)
from linersync.server.auth import require_session
from linersync.server.flags import FlagInfo, UnknownFlag
from linersync.server.state import AppServices

UNKNOWN_FLAG = "Unknown feature flag"

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


# -- feature flags --------------------------------------------------------------
def _flag_out(services: AppServices, info: FlagInfo) -> FlagOut:
    flag = info.flag
    affected: list[FlagSyncOut] = []
    if flag.affects_full_syncs:
        affected = [
            FlagSyncOut(sync_id=g.sync_id, name=g.name)
            for g in services.repos.syncs.list()
            if any(
                m.service == info.service
                and m.role is not MemberRole.SOURCE
                and m.mode is SyncMode.FULL
                for m in g.members
            )
        ]
    return FlagOut(
        flag_id=info.flag_id,
        service=info.service,
        service_name=info.service_name,
        title=flag.title,
        description=flag.description,
        on=flag.on,
        confirm=flag.confirm,
        unavailable=flag.unavailable,
        locked_by=flag.locked_by,
        affected_syncs=affected,
    )


@router.get("/flags")
async def list_flags(services: Services) -> list[FlagOut]:
    return [_flag_out(services, info) for info in services.flags.list()]


@router.put("/flags/{flag_id}")
async def put_flag(flag_id: str, body: FlagIn, services: Services) -> FlagOut:
    try:
        info = services.flags.set(flag_id, body.on)
    except UnknownFlag:
        raise HTTPException(status_code=404, detail=UNKNOWN_FLAG) from None
    except FlagUnavailable as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    return _flag_out(services, info)
