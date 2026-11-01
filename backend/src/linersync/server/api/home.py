"""Home panels: jobs, what runs next, needs-attention count, warnings, API limits."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends

from linersync.application.backups import BACKUP_ALL_LABEL
from linersync.domain.jobs import JobStatus
from linersync.ports.quota import QuotaPolicy, quota_day_start
from linersync.server.api.common import Services
from linersync.server.api.schemas import (
    HomeOut,
    JobOut,
    LimitOut,
    NextUpOut,
    WarningOut,
)
from linersync.server.auth import require_session
from linersync.server.connections import ConnectionInfo, ConnectionStatus
from linersync.server.state import AppServices

router = APIRouter(prefix="/api/home", dependencies=[Depends(require_session)])

LOGIN_EXPIRY_WARNING = timedelta(days=14)
NEXT_UP_LIMIT = 10
RECENT_LIMIT = 20


def next_quota_day_start(now: datetime, policy: QuotaPolicy) -> datetime:
    """The start of the quota day after the one containing `now`, in UTC."""
    zone = ZoneInfo(policy.reset_timezone)
    local = quota_day_start(now, policy).astimezone(zone)
    tomorrow = local.date() + timedelta(days=1)
    midnight = datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=zone)
    return midnight.astimezone(UTC)


def _next_up(services: AppServices) -> list[NextUpOut]:
    groups = {g.sync_id: g for g in services.repos.syncs.list()}
    upcoming = sorted(
        (at, sync_id)
        for sync_id, at in services.scheduler.next_runs().items()
        if sync_id in groups
    )
    entries = [
        NextUpOut(kind="sync", sync_id=sync_id, name=groups[sync_id].name, at=at)
        for at, sync_id in upcoming
    ]
    backup_at = services.scheduler.next_backup_at()
    if backup_at is not None:
        entries.append(
            NextUpOut(kind="backup", sync_id=None, name=BACKUP_ALL_LABEL, at=backup_at)
        )
    entries.sort(key=lambda n: n.at)
    return entries[:NEXT_UP_LIMIT]


def _warnings(services: AppServices, info: ConnectionInfo) -> list[WarningOut]:
    name, service = info.display_name, info.service
    now = services.clock.now()
    zone = ZoneInfo(services.config.timezone)
    out: list[WarningOut] = []
    if info.status is ConnectionStatus.NEEDS_SIGN_IN:
        out.append(
            WarningOut(
                service=service,
                kind="needs_sign_in",
                message=f"Sign in to {name} again",
            )
        )
    elif info.status is ConnectionStatus.FAILING:
        out.append(WarningOut(service=service, kind="failing", message=info.message))
    expires = info.login_expires_at
    if expires is not None and expires - now <= LOGIN_EXPIRY_WARNING:
        day = f"{expires.astimezone(zone):%d %b %Y}"
        message = (
            f"{name} may sign you out around {day}; "
            "sign in again before then to be safe"
            if info.login_expiry_estimated
            else f"{name} sign-in expires on {day}"
        )
        out.append(WarningOut(service=service, kind="login_expiring", message=message))
    opened_at = services.repos.breakers.get(service).opened_at
    if opened_at is not None:
        cooldown = services.settings_store.get().engine.breaker.cooldown
        until = (opened_at + cooldown).astimezone(zone)
        out.append(
            WarningOut(
                service=service,
                kind="service_paused",
                message=f"{name} keeps failing; "
                f"its syncs are paused until {until:%H:%M}",
            )
        )
    return out


def _limit(services: AppServices, info: ConnectionInfo) -> LimitOut:
    policy = services.connections.plugin(info.service).quota_policy
    now = services.clock.now()
    return LimitOut(
        service=info.service,
        display_name=info.display_name,
        used=services.quota.used_since(info.service, quota_day_start(now, policy)),
        limit=policy.daily_limit,
        resets_at=next_quota_day_start(now, policy),
    )


@router.get("")
async def home(services: Services) -> HomeOut:
    store = services.job_store
    set_up = services.connections.set_up()
    return HomeOut(
        running=[JobOut.of(j) for j in store.list_by_status(JobStatus.RUNNING)],
        queued=[JobOut.of(j) for j in store.list_by_status(JobStatus.QUEUED)],
        next_up=_next_up(services),
        recent=[JobOut.of(j) for j in store.list_recent(RECENT_LIMIT)],
        attention_count=len(services.repos.attention.list_open()),
        warnings=[w for info in set_up for w in _warnings(services, info)],
        api_limits=[_limit(services, info) for info in set_up],
    )
