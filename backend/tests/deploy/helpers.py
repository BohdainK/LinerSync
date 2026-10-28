"""Shared helpers for the Docker tests."""

import functools
import os
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

PW = "correct horse"
SAME_MACHINE = ["1", "8000", "./data", "Europe/Amsterdam", PW, PW]
BUNDLED_LE = [
    "2",
    "music.example.com",
    "1",
    "admin@example.com",
    "./data",
    "Europe/Amsterdam",
    PW,
    PW,
]

OWN_PROXY = [
    "3",
    "https://music.example.com",
    "1",
    "y",
    "8000",
    "./data",
    "Europe/Amsterdam",
    PW,
    PW,
]

_built: str | None = None


@functools.cache
def docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(
            ["docker", "info"], capture_output=True, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def image_tag() -> str:
    """The image to test: $LINERSYNC_TEST_IMAGE, else build `linersync:test` once."""
    global _built
    preset = os.environ.get("LINERSYNC_TEST_IMAGE")
    if preset:
        return preset
    if _built is None:
        build = subprocess.run(
            ["docker", "build", "-t", "linersync:test", str(REPO)],
            capture_output=True,
            text=True,
            timeout=1200,
            check=False,
        )
        if build.returncode != 0:
            raise RuntimeError(f"docker build failed:\n{build.stderr[-3000:]}")
        _built = "linersync:test"
    return _built


def run_install(
    target: Path, answers: list[str], *, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run install.sh --dir target, piping one answer per line."""
    env = {**os.environ, "LINERSYNC_UID": "1000", "LINERSYNC_GID": "1000"}
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(REPO / "install.sh"), "--dir", str(target)],
        input="\n".join(answers) + "\n",
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
        check=False,
    )
