"""Export routes: CSV of backed-up playlists, the full JSON export and its
import (merge restore)."""

import json
from importlib.metadata import version
from typing import Annotated
from urllib.parse import quote
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
from linersync.server.api.schemas import ExportOut, RestoreReportOut
from linersync.server.api.syncs import (
    UNKNOWN_BACKUP,
    backup_unlocked,
    reconcile_schedule,
)
from linersync.server.auth import require_session
from linersync.server.state import AppServices, get_services

router = APIRouter(prefix="/api/export", dependencies=[Depends(require_session)])
import_router = APIRouter(dependencies=[Depends(require_session)])

Services = Annotated[AppServices, Depends(get_services)]

NOTHING_BACKED_UP = "Nothing has been backed up yet"
CSV_TYPE = "text/csv; charset=utf-8"
MAX_IMPORT_BYTES = 256 * 1024 * 1024
TOO_LARGE = "The file is too large"


def attachment(filename: str) -> str:
    """A Content-Disposition value with an ASCII fallback and the UTF-8 name."""
    fallback = "".join(c if c.isascii() and c.isprintable() else "_" for c in filename)
    fallback = fallback.replace("\\", "_").replace('"', "_")
    return (
        f'attachment; filename="{fallback}"; '
        f"filename*=UTF-8''{quote(filename, safe='')}"
    )


def csv_response(body: str, filename: str) -> Response:
    return Response(
        content=body.encode("utf-8-sig"),
        media_type=CSV_TYPE,
        headers={"Content-Disposition": attachment(filename)},
    )


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
    if entry_id:
        entries: list[BackedUpPlaylist] = []
        for eid in dict.fromkeys(entry_id):
            found = store.get_entry(eid)
            if found is None:
                raise HTTPException(status_code=404, detail=UNKNOWN_BACKUP)
            entries.append(found)
    else:
        entries = store.list_entries()
    if not entries:
        raise HTTPException(status_code=404, detail=NOTHING_BACKED_UP)
    ids = known_ids(services.repos.syncs.list(), services.repos.matches.list_all())
    files: list[tuple[str, str]] = []
    for entry in entries:
        version = store.get_version(entry.latest_version_id)
        if version is None:
            continue
        name = csv_filename(version.snapshot.meta.name, entry.service)
        text = playlist_csv(version.snapshot, entry.service, ids)
        files.append((f"archive/{name}" if entry.archived else name, text))
    if not files:
        raise HTTPException(status_code=404, detail=NOTHING_BACKED_UP)
    if len(files) == 1:
        path, text = files[0]
        return csv_response(text, path.removeprefix("archive/"))
    now = services.clock.now().astimezone(ZoneInfo(services.config.timezone))
    name = f"linersync-csv-{zip_stamp(now)}.zip"
    return Response(
        content=csv_zip(sorted(files, key=lambda f: f[0].startswith("archive/"))),
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


def _local_stamp(services: AppServices) -> str:
    now = services.clock.now().astimezone(ZoneInfo(services.config.timezone))
    return zip_stamp(now)


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


def _unprocessable(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


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
        raise _unprocessable(NOT_AN_EXPORT) from None
    try:
        check_header(data)
    except ValueError as e:
        raise _unprocessable(str(e)) from None
    try:
        doc: ExportDocument = decode(ExportDocument, data, strict=True)
        check_covers(doc)
    except ValueError as e:  # strict decode turns every failure into ValueError
        raise _unprocessable(DAMAGED_EXPORT.format(detail=str(e))) from None
    backup_unlocked(services)
    try:
        report = _export_service(services).restore(doc)
    except ValueError as e:  # cover data that isn't base64; nothing written
        raise _unprocessable(DAMAGED_EXPORT.format(detail=str(e))) from None
    reconcile_schedule(services)
    return RestoreReportOut(
        added=report.added,
        skipped=report.skipped,
        settings_applied=report.settings_applied,
    )
