"""Serve the built web app, with a fallback to index.html for deep links."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, Response

_SECURITY_HEADERS = {"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY"}
_NO_CACHE = {"Cache-Control": "no-cache", **_SECURITY_HEADERS}
_IMMUTABLE = {
    "Cache-Control": "public, max-age=31536000, immutable",
    **_SECURITY_HEADERS,
}


def mount_web(app: FastAPI, web_dir: Path) -> None:
    """Add the catch-all route. Call after every other route."""
    root = web_dir.resolve()
    index = root / "index.html"

    @app.get("/{path:path}", include_in_schema=False)
    async def web(path: str) -> Response:
        if path == "api" or path.startswith("api/"):
            return JSONResponse(
                {"detail": "Not Found"}, status_code=404, headers=_SECURITY_HEADERS
            )
        target = _existing_file(root, path)
        if target is not None:
            headers = _IMMUTABLE if path.startswith("assets/") else _NO_CACHE
            return FileResponse(target, headers=headers)
        if "." in path.rsplit("/", 1)[-1]:
            return JSONResponse(
                {"detail": "Not Found"}, status_code=404, headers=_SECURITY_HEADERS
            )
        return FileResponse(index, headers=_NO_CACHE)


def _existing_file(root: Path, path: str) -> Path | None:
    if not path:
        return None
    try:
        target = (root / path).resolve()
        if target.is_relative_to(root) and target.is_file():
            return target
    except (ValueError, OSError):
        pass
    return None
