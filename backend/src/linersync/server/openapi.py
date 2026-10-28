"""Dump the API's OpenAPI document: `python -m linersync.server.openapi`."""

import json
import tempfile
from pathlib import Path
from typing import Any

from linersync.adapters.connectors.kit.discovery import PluginRegistry
from linersync.server.app import create_app
from linersync.server.config import ServerConfig


def openapi_document() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as data_dir:
        app = create_app(
            ServerConfig(admin_password="openapi", data_dir=Path(data_dir)),
            registry=PluginRegistry({}, ()),
            start_worker=False,
        )
        return app.openapi()


def main() -> None:
    print(json.dumps(openapi_document(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
