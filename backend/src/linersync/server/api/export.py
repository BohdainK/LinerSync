"""Export routes: CSV of backed-up playlists, the full JSON export and its
import (merge restore)."""

import json
from importlib.metadata import version
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from linersync.adapters.sqlite.codec import decode, dumps
from linersync.application.export_csv import (
    csv_filename,
    csv_zip,
    known_ids,
    playlist_csv,
    zip_stamp,
)
from linersync.application.export_json import (
    DAMAGED_EXPORT,
    NOT_AN_EXPORT,
    ExportDocument,
    ExportService,
    check_covers,
    check_header,
)
from linersync.domain.backups import BackedUpPlaylist
from linersync.server.api.common import (
    UNKNOWN_BACKUP,
    Services,
    attachment,
    backup_unlocked,
    csv_response,
    found,
    invalid,
    reconcile_schedule,
)
from linersync.server.api.schemas import ExportOut, RestoreReportOut
from linersync.server.auth import require_session
from linersync.server.state import AppServices

router = APIRouter(prefix="/api/export", dependencies=[Depends(require_session)])
import_router = APIRouter(dependencies=[Depends(require_session)])

NOTHING_BACKED_UP = "Nothing has been backed up yet"
MAX_IMPORT_BYTES = 256 * 1024 * 1024
TOO_LARGE = "The file is too large"


def _local_stamp(services: AppServices) -> str:
    now = services.clock.now().astimezone(ZoneInfo(services.config.timezone))
    return zip_stamp(now)


@router.get(
    "/csv",
    responses={
        200: {
            "content": {"text/csv": {}, "application/zip": {}},
            "description": "One playlist as CSV, several as a zip",
        }
    },
)
async def export_csv(
    services: Services, entry_id: Annotated[list[str] | None, Query()] = None
) -> Response:
    store = services.backups
    entries: list[BackedUpPlaylist] = (
        [found(store.get_entry(eid), UNKNOWN_BACKUP) for eid in dict.fromkeys(entry_id)]
        if entry_id
        else store.list_entries()
    )
    if not entries:
        raise HTTPException(status_code=404, detail=NOTHING_BACKED_UP)
    ids = known_ids(services.repos.syncs.list(), services.repos.matches.list_all())
    files: list[tuple[bool, str, str]] = []  # (archived, file name, csv)
    for entry in entries:
        version = store.get_version(entry.latest_version_id)
        if version is None:
            continue
        name = csv_filename(version.snapshot.meta.name, entry.service)
        text = playlist_csv(version.snapshot, entry.service, ids)
        files.append((entry.archived, name, text))
    if not files:
        raise HTTPException(status_code=404, detail=NOTHING_BACKED_UP)
    if len(files) == 1:
        _, name, text = files[0]
        return csv_response(text, name)
    files.sort(key=lambda f: f[0])  # archived playlists last
    paths = [(f"archive/{n}" if archived else n, text) for archived, n, text in files]
    name = f"linersync-csv-{_local_stamp(services)}.zip"
    return Response(
        content=csv_zip(paths),
        media_type="application/zip",
        headers={"Content-Disposition": attachment(name)},
    )


# -- JSON export and import (D8, D9) -------------------------------------------
def _export_service(services: AppServices) -> ExportService:
    return ExportService(
        services.repos,
        services.contexts,
        services.backups,
        services.settings_store,
        services.clock,
    )


@router.get("", response_model=ExportOut)
async def export_json(services: Services) -> Response:
    doc = _export_service(services).build(version("linersync"))
    name = f"linersync-export-{_local_stamp(services)}.json"
    return Response(
        content=dumps(ExportDocument, doc).encode("utf-8"),
        media_type="application/json",
        headers={"Content-Disposition": attachment(name)},
    )


def _too_large() -> HTTPException:
    return HTTPException(status_code=413, detail=TOO_LARGE)


async def _read_body(request: Request) -> bytes:
    """The body, refusing anything longer than MAX_IMPORT_BYTES."""
    limit = MAX_IMPORT_BYTES
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise _too_large()
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise _too_large()
    return bytes(body)


@import_router.post(
    "/api/import",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": {"type": "object"}}},
        }
    },
)
async def import_json(request: Request, services: Services) -> RestoreReportOut:
    body = await _read_body(request)
    try:
        data = json.loads(body)
    except ValueError:  # JSONDecodeError and UnicodeDecodeError
        raise invalid(NOT_AN_EXPORT) from None
    try:
        check_header(data)
    except ValueError as e:
        raise invalid(str(e)) from None
    try:
        doc: ExportDocument = decode(ExportDocument, data, strict=True)
        check_covers(doc)
    except ValueError as e:  # strict decode turns every failure into ValueError
        raise invalid(DAMAGED_EXPORT.format(detail=str(e))) from None
    backup_unlocked(services)
    try:
        report = _export_service(services).restore(doc)
    except ValueError as e:  # cover data that isn't base64; nothing written
        raise invalid(DAMAGED_EXPORT.format(detail=str(e))) from None
    reconcile_schedule(services)
    return RestoreReportOut.of(report)
