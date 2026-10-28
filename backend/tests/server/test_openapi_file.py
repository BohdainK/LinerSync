import json
from pathlib import Path

from linersync.server.openapi import openapi_document

REPO = Path(__file__).resolve().parents[3]


def test_frontend_openapi_file_is_current() -> None:
    committed = json.loads((REPO / "frontend/openapi.json").read_text())
    assert committed == openapi_document(), (
        "frontend/openapi.json is stale. Regenerate it from backend/ with: "
        "uv run python -m linersync.server.openapi > ../frontend/openapi.json"
    )


def test_openapi_document_lists_core_routes() -> None:
    paths = openapi_document()["paths"]
    for path in ("/api/home", "/api/events", "/api/backups/playlists", "/api/import"):
        assert path in paths
