"""The `linersync` command line: `linersync serve` runs the server."""

import argparse
import os
import sys
from collections.abc import Sequence

import uvicorn

from linersync.server.app import create_app
from linersync.server.config import ConfigError, ServerConfig, check_data_dir

# Open event streams would otherwise hold shutdown forever: after this, uvicorn
# cancels them and runs the lifespan shutdown.
GRACEFUL_SHUTDOWN_SECONDS = 5


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="linersync",
        description="LinerSync server. Configure it with LINERSYNC_* variables.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("serve", help="run the web server")
    parser.parse_args(argv)
    try:
        config = ServerConfig.from_env(os.environ)
        check_data_dir(config.data_dir)
    except ConfigError as exc:
        print(f"linersync: {exc}", file=sys.stderr)
        return 2
    uvicorn.run(
        create_app(config),
        host=config.host,
        port=config.port,
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS,
    )
    return 0
