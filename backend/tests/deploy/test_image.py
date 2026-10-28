import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from tests.deploy.helpers import docker_available, image_tag

pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(not docker_available(), reason="docker is not available"),
]

NOT_ROOT = pytest.mark.skipif(os.getuid() == 0, reason="root ignores folder modes")


def docker(*args: str, timeout: float = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def wait_for(check: Callable[[], bool], seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.5)
    return check()


@contextmanager
def container(*extra: str, keep: bool = False) -> Iterator[str]:
    """Run the image detached; `keep` leaves it after exit so tests can inspect it."""
    started = docker(
        "run",
        *([] if keep else ["--rm"]),
        "-d",
        "-e",
        "LINERSYNC_ADMIN_PASSWORD=pw",
        "-p",
        "127.0.0.1::8000",
        *extra,
        image_tag(),
        timeout=120,
    )
    assert started.returncode == 0, started.stderr
    cid = started.stdout.strip()
    try:
        yield cid
    finally:
        docker("rm", "-f", cid, timeout=60)


def base_url(cid: str) -> str:
    out = docker("port", cid, "8000/tcp").stdout.splitlines()[0]
    return f"http://127.0.0.1:{out.rsplit(':', 1)[1]}"


def get(url: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except (OSError, ValueError):
        return 0, ""


def wait_healthy_api(cid: str, seconds: float = 60) -> str:
    url = base_url(cid)
    assert wait_for(lambda: get(url + "/api/health")[0] == 200, seconds), (
        docker("logs", cid).stdout + docker("logs", cid).stderr
    )
    return url


def health_status(cid: str) -> str:
    return docker("inspect", "-f", "{{.State.Health.Status}}", cid).stdout.strip()


def open_folder(tmp_path: Path) -> Path:
    folder = tmp_path / "data"
    folder.mkdir()
    folder.chmod(0o777)
    return folder


def test_image_serves_app_and_api() -> None:
    with container() as cid:
        url = wait_healthy_api(cid)
        status, body = get(url + "/api/health")
        assert status == 200
        assert "version" in json.loads(body)
        status, body = get(url + "/")
        assert status == 200
        assert '<div id="root">' in body
        assert wait_for(lambda: health_status(cid) == "healthy", 90)


def test_image_runs_as_non_root() -> None:
    with container() as cid:
        wait_healthy_api(cid)
        assert docker("exec", cid, "id", "-u").stdout.strip() == "1000"


def test_image_keeps_data_in_volume(tmp_path: Path) -> None:
    folder = open_folder(tmp_path)
    with container("-v", f"{folder}:/data") as cid:
        wait_healthy_api(cid)
    assert (folder / "linersync.db").is_file()
    assert (folder / "secret.key").is_file()


def test_image_runs_as_other_user(tmp_path: Path) -> None:
    folder = open_folder(tmp_path)
    with container("--user", "4321:4321", "-v", f"{folder}:/data") as cid:
        wait_healthy_api(cid)


@NOT_ROOT
def test_image_reports_unwritable_data_dir(tmp_path: Path) -> None:
    folder = tmp_path / "data"
    folder.mkdir()
    folder.chmod(0o555)
    try:
        probe = docker(
            "run", "--rm", "-v", f"{folder}:/d", "--user", "1000",
            "busybox", "touch", "/d/x",
            timeout=120,
        )  # fmt: skip
        if probe.returncode == 0:
            pytest.skip("Docker file sharing ignores the folder mode")
        with container("-v", f"{folder}:/data", keep=True) as cid:
            assert wait_for(
                lambda: (
                    docker("inspect", "-f", "{{.State.Running}}", cid).stdout.strip()
                    != "true"
                ),
                30,
            )
            code = docker("inspect", "-f", "{{.State.ExitCode}}", cid).stdout.strip()
            logs = docker("logs", cid)
            assert code == "2", logs.stderr
            assert "can't write to the data folder /data" in logs.stdout + logs.stderr
    finally:
        folder.chmod(0o755)


def test_image_stops_gracefully() -> None:
    with container(keep=True) as cid:
        wait_healthy_api(cid)
        started = time.monotonic()
        stopped = docker("stop", "-t", "15", cid, timeout=40)
        assert stopped.returncode == 0, stopped.stderr
        assert time.monotonic() - started < 10
        assert docker("inspect", "-f", "{{.State.ExitCode}}", cid).stdout.strip() == "0"
