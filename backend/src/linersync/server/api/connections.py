"""Connection setup routes."""

import importlib
import importlib.resources
import mimetypes
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import PurePosixPath
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from linersync.domain.tracks import ServiceId
from linersync.ports.errors import ConnectorError
from linersync.ports.setup import SetupGuide
from linersync.server.api.common import backup_unlocked, connector_failed
from linersync.server.api.schemas import (
    ConnectionListOut,
    ConnectionOut,
    ConnectionTestOut,
    CredentialsIn,
    DeviceCodeOut,
    DevicePollOut,
    LoadErrorOut,
    OAuthStartOut,
    PlaylistOut,
    SignInConfigOut,
    SignInResultIn,
)
from linersync.server.auth import require_session
from linersync.server.connections import (
    ConnectionManager,
    NotSetUp,
    OAuthFailed,
    WrongSetup,
)
from linersync.server.state import get_services

router = APIRouter(prefix="/api/connections", dependencies=[Depends(require_session)])


def _manager(request: Request) -> ConnectionManager:
    return get_services(request).connections


async def _known_service(service: str, request: Request) -> ServiceId:
    if service not in get_services(request).registry.plugins:
        raise HTTPException(status_code=404, detail="Unknown service")
    return ServiceId(service)


Manager = Annotated[ConnectionManager, Depends(_manager)]
Service = Annotated[ServiceId, Depends(_known_service)]


@contextmanager
def _conflicts() -> Iterator[None]:
    """A sign-in step the service doesn't use, or isn't set up for, is 409."""
    try:
        yield
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None


@router.get("")
async def list_connections(request: Request, manager: Manager) -> ConnectionListOut:
    registry = get_services(request).registry
    return ConnectionListOut(
        services=[ConnectionOut.of(info) for info in manager.list()],
        load_errors=[
            LoadErrorOut(name=e.name, message=e.message) for e in registry.errors
        ],
    )


@router.get("/oauth/callback", status_code=303, response_class=RedirectResponse)
async def oauth_callback(
    request: Request,
    manager: Manager,
    state: str | None = None,
    code: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    public = get_services(request).config.public_url.rstrip("/")
    try:
        service = await manager.finish_oauth(state, code, error)
    except OAuthFailed as e:
        page = f"/connections/{e.service}" if e.service else "/connections"
        query = urlencode({"sign_in_error": e.message})
        return RedirectResponse(f"{public}{page}?{query}", status_code=303)
    return RedirectResponse(
        f"{public}/connections/{service}?signed_in=1", status_code=303
    )


@router.get("/{service}")
async def get_connection(service: Service, manager: Manager) -> ConnectionOut:
    return ConnectionOut.of(manager.info(service))


@router.get("/{service}/guide")
async def get_guide(service: Service, manager: Manager) -> SetupGuide:
    return manager.guide(service)


@router.get(
    "/{service}/resources/{path:path}",
    responses={200: {"content": {"image/*": {}}, "description": "The file"}},
)
async def get_resource(service: Service, path: str, manager: Manager) -> Response:
    referenced = {
        shot.resource
        for step in manager.plugin(service).setup_guide().steps
        for shot in step.screenshots
    }
    parts = PurePosixPath(path).parts
    if path not in referenced or path.startswith("/") or ".." in parts:
        raise HTTPException(status_code=404, detail="Not found")
    module_name = type(manager.plugin(service)).__module__
    module = importlib.import_module(module_name)
    package = (
        module_name if hasattr(module, "__path__") else module_name.rpartition(".")[0]
    )
    resource = importlib.resources.files(package).joinpath(*parts)
    if not resource.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    media_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
    return Response(resource.read_bytes(), media_type=media_type)


@router.put("/{service}/credentials")
async def put_credentials(
    service: Service, body: CredentialsIn, manager: Manager
) -> ConnectionOut:
    problems = manager.save_values(service, body.values)
    if problems:
        raise HTTPException(status_code=422, detail={"problems": problems})
    return ConnectionOut.of(manager.info(service))


@router.post("/{service}/test")
async def test_connection(service: Service, manager: Manager) -> ConnectionTestOut:
    return ConnectionTestOut.of(await manager.test(service))


@router.post("/{service}/oauth/start")
async def oauth_start(service: Service, manager: Manager) -> OAuthStartOut:
    with _conflicts():
        return OAuthStartOut(url=manager.start_oauth(service))


@router.post("/{service}/device-code/start")
async def device_code_start(service: Service, manager: Manager) -> DeviceCodeOut:
    with _conflicts():
        start = await manager.start_device_code(service)
    return DeviceCodeOut.of(start)


@router.post("/{service}/device-code/poll")
async def device_code_poll(service: Service, manager: Manager) -> DevicePollOut:
    with _conflicts():
        result = await manager.poll_device_code(service)
    return DevicePollOut.of(result)


@router.get("/{service}/browser-sign-in")
async def browser_sign_in_config(service: Service, manager: Manager) -> SignInConfigOut:
    with _conflicts():
        return SignInConfigOut(config=manager.sign_in_config(service))


@router.post("/{service}/browser-sign-in")
async def browser_sign_in_result(
    service: Service, body: SignInResultIn, manager: Manager
) -> ConnectionOut:
    # 409 for the wrong setup; the plugin rejecting the result is 422.
    try:
        manager.accept_sign_in(service, body.result)
    except (WrongSetup, NotSetUp) as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    return ConnectionOut.of(manager.info(service))


@router.delete("/{service}", status_code=204)
async def disconnect(
    service: Service, request: Request, delete_backups: bool = False
) -> Response:
    """Backups stay unless `delete_backups` is set (D11)."""
    services = get_services(request)
    if delete_backups:
        backup_unlocked(services)
    services.connections.disconnect(service)
    if delete_backups:
        services.backups.delete_service(service)
        services.backups.prune_covers()
    return Response(status_code=204)


@router.get("/{service}/playlists")
async def list_playlists(service: Service, manager: Manager) -> list[PlaylistOut]:
    try:
        playlists = await manager.playlists(service)
    except NotSetUp as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    except ConnectorError as e:
        raise connector_failed(manager, e, service) from None
    return [PlaylistOut.of(meta) for meta in playlists]
