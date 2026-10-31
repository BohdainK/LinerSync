"""Stats: library size, recent activity and matching, from stored data only."""

from dataclasses import asdict
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query

from linersync.application import stats
from linersync.domain.sync import SyncStatus
from linersync.domain.tracks import ServiceId
from linersync.server.api.schemas import (
    ActivityOut,
    DayOut,
    LibraryServiceOut,
    MatchingOut,
    StatsOut,
)
from linersync.server.auth import require_session
from linersync.server.state import AppServices, get_services

router = APIRouter(prefix="/api/stats", dependencies=[Depends(require_session)])

Services = Annotated[AppServices, Depends(get_services)]


def _display_name(services: AppServices, service: ServiceId) -> str:
    plugin = services.registry.plugins.get(service)
    return plugin.display_name if plugin is not None else service


@router.get("")
async def get_stats(
    services: Services, days: Annotated[int, Query(ge=1, le=365)] = 30
) -> StatsOut:
    zone = ZoneInfo(services.config.timezone)
    since = stats.window_start(services.clock.now(), days, zone)
    runs = services.repos.runs.list_since(since)
    library = [
        LibraryServiceOut(
            display_name=_display_name(services, row.service), **asdict(row)
        )
        for row in stats.library(services.backups.list_entries())
    ]
    library.sort(key=lambda o: (o.display_name.casefold(), o.service))
    groups = services.repos.syncs.list()
    backup_runs = services.backups.list_runs()
    activity = stats.activity(
        runs,
        (r for r in backup_runs if r.started_at >= since),
    )
    matching = stats.matching(services.repos.matches.list_all())
    return StatsOut(
        days=days,
        since=since,
        library=library,
        syncs=len(groups),
        paused_syncs=sum(1 for g in groups if g.status is SyncStatus.PAUSED),
        activity=ActivityOut(**asdict(activity)),
        daily=[
            DayOut(**asdict(d))
            for d in stats.daily(runs, since.astimezone(zone).date(), days, zone)
        ],
        last_backup_at=backup_runs[0].started_at if backup_runs else None,
        matching=MatchingOut(
            **asdict(matching),
            open_attention=len(services.repos.attention.list_open()),
        ),
    )
