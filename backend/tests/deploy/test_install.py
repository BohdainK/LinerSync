import json
import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

from tests.deploy.helpers import (
    BUNDLED_LE,
    OWN_PROXY,
    PW,
    REPO,
    SAME_MACHINE,
    docker_available,
    run_install,
)

EXAMPLES = REPO / "deploy" / "examples"
CALLBACK = "/api/connections/oauth/callback"
TRICKY_PW = 'pa$$ w"rd#1'


def ok(result: subprocess.CompletedProcess[str]) -> subprocess.CompletedProcess[str]:
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def test_same_machine_matches_example(tmp_path: Path) -> None:
    out = tmp_path / "ls"
    result = ok(run_install(out, SAME_MACHINE))
    assert (out / "docker-compose.yml").read_text() == (
        EXAMPLES / "same-machine" / "docker-compose.yml"
    ).read_text()
    env = out / ".env"
    assert env.read_text() == "LINERSYNC_ADMIN_PASSWORD='correct horse'\n"
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert (out / "data").is_dir()
    assert not (out / "Caddyfile").exists()
    assert f"http://127.0.0.1:8000{CALLBACK}" in result.stdout
    assert "docker compose up -d" in result.stdout


def test_bundled_proxy_matches_example(tmp_path: Path) -> None:
    out = tmp_path / "ls"
    result = ok(run_install(out, BUNDLED_LE))
    example = EXAMPLES / "bundled-proxy"
    assert (out / "docker-compose.yml").read_text() == (
        example / "docker-compose.yml"
    ).read_text()
    assert (out / "Caddyfile").read_text() == (example / "Caddyfile").read_text()
    assert f"https://music.example.com{CALLBACK}" in result.stdout
    assert "ports 80 and 443" in result.stdout


def test_bundled_proxy_local_ca(tmp_path: Path) -> None:
    out = tmp_path / "ls"
    answers = ["2", "music.lan", "2", "8443", "./data", "Europe/Amsterdam", PW, PW]
    result = ok(run_install(out, answers))
    assert (out / "Caddyfile").read_text() == (
        "music.lan:8443 {\n\ttls internal\n\treverse_proxy linersync:8000\n}\n"
    )
    compose = (out / "docker-compose.yml").read_text()
    assert '"8443:8443"' in compose
    assert 'LINERSYNC_PUBLIC_URL: "https://music.lan:8443"' in compose
    assert (
        "docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt"
        in result.stdout
    )


OWN_PROXY_EXAMPLES = EXAMPLES / "own-proxy"


@pytest.mark.parametrize(
    ("choice", "name", "file"),
    [
        ("1", "Caddy", "Caddyfile"),
        ("2", "Traefik", "traefik.yml"),
        ("3", "Nginx", "nginx.conf"),
    ],
)
def test_own_proxy_matches_example(
    tmp_path: Path, choice: str, name: str, file: str
) -> None:
    out = tmp_path / "ls"
    answers = [*OWN_PROXY]
    answers[2] = choice
    result = ok(run_install(out, answers))
    assert (out / "docker-compose.yml").read_text() == (
        OWN_PROXY_EXAMPLES / "docker-compose.yml"
    ).read_text()
    assert not (out / "Caddyfile").exists()
    assert f"Add this to your {name} configuration:" in result.stdout
    assert (OWN_PROXY_EXAMPLES / file).read_text() in result.stdout
    assert f"https://music.example.com{CALLBACK}" in result.stdout


def test_own_proxy_other_prints_requirements(tmp_path: Path) -> None:
    answers = [*OWN_PROXY]
    answers[2] = "4"
    stdout = ok(run_install(tmp_path / "ls", answers)).stdout
    for text in (
        "http://127.0.0.1:8000",
        "Origin",
        "Content-Type",
        "/api/events",
        "256 MB",
        "docs/reverse-proxy.md",
    ):
        assert text in stdout


def test_own_proxy_on_another_computer(tmp_path: Path) -> None:
    out = tmp_path / "ls"
    answers = [*OWN_PROXY]
    answers[3] = "n"
    result = ok(run_install(out, answers))
    compose = (out / "docker-compose.yml").read_text()
    assert '- "8000:8000"' in compose
    assert "127.0.0.1:" not in compose
    assert (
        "LinerSync's port 8000 is open to your network without HTTPS. "
        "Let only your proxy reach it."
    ) in result.stdout
    assert "reverse_proxy ADDRESS-OF-THIS-COMPUTER:8000" in result.stdout


def test_own_proxy_rejects_http_and_paths(tmp_path: Path) -> None:
    out = tmp_path / "ls"
    answers = [
        "3", "http://music.example.com", "https://music.example.com/app",
        "https://music.example.com/", "1", "y", "8000", "./data",
        "Europe/Amsterdam", PW, PW,
    ]  # fmt: skip
    result = ok(run_install(out, answers))
    assert "Use the https:// address; your proxy handles HTTPS." in result.stdout
    assert "without a path" in result.stdout
    assert (
        'LINERSYNC_PUBLIC_URL: "https://music.example.com"'
        in (out / "docker-compose.yml").read_text()
    )


def test_own_proxy_address_with_port(tmp_path: Path) -> None:
    out = tmp_path / "ls"
    answers = [*OWN_PROXY]
    answers[1] = "https://music.example.com:8443"
    compose = out / "docker-compose.yml"
    caddy = ok(run_install(out, answers)).stdout
    assert "music.example.com:8443 {" in caddy
    assert (
        'LINERSYNC_PUBLIC_URL: "https://music.example.com:8443"' in compose.read_text()
    )
    answers[2] = "3"
    nginx = ok(run_install(out, ["y", *answers])).stdout
    assert "listen 8443 ssl;" in nginx
    assert "server_name music.example.com;" in nginx


@pytest.mark.parametrize(("choice", "expected"), [
    ("2", 'url: "http://ADDRESS-OF-THIS-COMPUTER:8000"'),
    ("3", "proxy_pass http://ADDRESS-OF-THIS-COMPUTER:8000;"),
])  # fmt: skip
def test_own_proxy_remote_upstream(tmp_path: Path, choice: str, expected: str) -> None:
    answers = [*OWN_PROXY]
    answers[2] = choice
    answers[3] = "n"
    assert expected in ok(run_install(tmp_path / "ls", answers)).stdout


def test_own_proxy_rejects_out_of_range_port(tmp_path: Path) -> None:
    out = tmp_path / "ls"
    answers = [
        "3", "https://music.example.com:99999", "https://music.example.com:0",
        "https://music.example.com:8443", "1", "y", "8000", "./data",
        "Europe/Amsterdam", PW, PW,
    ]  # fmt: skip
    result = ok(run_install(out, answers))
    assert result.stdout.count("without a path") == 2
    assert (
        'LINERSYNC_PUBLIC_URL: "https://music.example.com:8443"'
        in (out / "docker-compose.yml").read_text()
    )


def test_nginx_example_allows_imports_and_streams() -> None:
    conf = (OWN_PROXY_EXAMPLES / "nginx.conf").read_text()
    for line in (
        "client_max_body_size 256m;",
        "proxy_buffering off;",
        "proxy_read_timeout 1h;",
        "proxy_http_version 1.1;",
        "proxy_set_header Host $host;",
    ):
        assert line in conf
    assert "proxy_set_header Origin" not in conf


def test_generates_password_when_empty(tmp_path: Path) -> None:
    out = tmp_path / "ls"
    result = ok(run_install(out, ["1", "8000", "./data", "Europe/Amsterdam", ""]))
    env = (out / ".env").read_text()
    match = re.fullmatch(r"LINERSYNC_ADMIN_PASSWORD='([A-Za-z0-9]{24})'\n", env)
    assert match is not None, env
    shown = result.stdout.split("shown once", 1)
    assert len(shown) == 2
    assert match.group(1) in shown[1]


def test_password_is_written_literally(tmp_path: Path) -> None:
    out = tmp_path / "ls"
    answers = ["1", "8000", "./data", "Europe/Amsterdam", TRICKY_PW, TRICKY_PW]
    ok(run_install(out, answers))
    assert (out / ".env").read_text() == f"LINERSYNC_ADMIN_PASSWORD='{TRICKY_PW}'\n"


def test_reprompts_on_invalid_answers(tmp_path: Path) -> None:
    out = tmp_path / "ls"
    answers = [
        "7", "1",
        "80a", "70000", "8000",
        "./data",
        "Mars/Base", "Europe/Amsterdam",
        "short", "it's long enough", PW, "different", PW, PW,
    ]  # fmt: skip
    result = ok(run_install(out, answers))
    stdout = result.stdout
    assert "Choose 1, 2 or 3." in stdout
    assert stdout.count("Enter a port from 1 to 65535.") == 2
    if Path("/usr/share/zoneinfo").is_dir():
        assert "Unknown timezone" in stdout
    assert "Use at least 8 characters." in stdout
    assert "The password can't contain '." in stdout
    assert "The passwords don't match." in stdout
    assert (out / "docker-compose.yml").read_text() == (
        EXAMPLES / "same-machine" / "docker-compose.yml"
    ).read_text()


def test_domain_with_scheme_is_refused(tmp_path: Path) -> None:
    out = tmp_path / "ls"
    answers = [
        "2", "https://music.example.com/", "music.example.com", "1", "",
        "./data", "Europe/Amsterdam", PW, PW,
    ]  # fmt: skip
    result = ok(run_install(out, answers))
    assert "without https:// or a path" in result.stdout
    caddyfile = (out / "Caddyfile").read_text()
    assert caddyfile == "music.example.com {\n\treverse_proxy linersync:8000\n}\n"


def test_input_ended_writes_nothing(tmp_path: Path) -> None:
    out = tmp_path / "ls"
    result = run_install(out, ["1", "8000"])
    assert result.returncode == 1
    assert "input ended" in result.stderr
    assert not out.exists()


def test_rerun_asks_before_replacing(tmp_path: Path) -> None:
    out = tmp_path / "ls"
    ok(run_install(out, SAME_MACHINE))
    keep = out / "data" / "keep.txt"
    keep.write_text("mine")
    (out / ".env").write_text("X")

    refused = run_install(out, ["n", *BUNDLED_LE])
    assert refused.returncode == 1
    assert "Nothing was changed." in refused.stdout
    assert (out / ".env").read_text() == "X"
    assert not (out / "Caddyfile").exists()

    ok(run_install(out, ["y", *BUNDLED_LE]))
    assert (out / "Caddyfile").exists()
    assert keep.read_text() == "mine"
    assert stat.S_IMODE((out / ".env").stat().st_mode) == 0o600

    ok(run_install(out, ["y", *SAME_MACHINE]))
    assert not (out / "Caddyfile").exists()
    assert keep.read_text() == "mine"


def test_help_and_bad_option() -> None:
    script = str(REPO / "install.sh")
    env = {**os.environ, "LINERSYNC_UID": "1000", "LINERSYNC_GID": "1000"}

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", script, *args],
            input="",
            capture_output=True,
            text=True,
            timeout=20,
            env=env,
            check=False,
        )

    helped = run("--help")
    assert helped.returncode == 0
    assert "Usage:" in helped.stdout
    bad = run("--nope")
    assert bad.returncode == 2
    assert "Usage:" in bad.stderr


def test_script_is_executable() -> None:
    assert os.access(REPO / "install.sh", os.X_OK)


def compose_config(out: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", "-f", str(out / "docker-compose.yml"),
         "config", "--format", "json"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )  # fmt: skip


@pytest.mark.docker
@pytest.mark.skipif(not docker_available(), reason="docker is not available")
def test_compose_files_are_valid(tmp_path: Path) -> None:
    for name, answers in (
        ("same", SAME_MACHINE),
        ("bundled", BUNDLED_LE),
        ("own", OWN_PROXY),
    ):
        out = tmp_path / name
        ok(run_install(out, answers))
        config = compose_config(out)
        assert config.returncode == 0, config.stderr
    for caddyfile in (
        tmp_path / "bundled" / "Caddyfile",
        OWN_PROXY_EXAMPLES / "Caddyfile",
    ):
        caddy = subprocess.run(
            ["docker", "run", "--rm",
             "-v", f"{caddyfile}:/etc/caddy/Caddyfile:ro",
             "caddy:2", "caddy", "validate",
             "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )  # fmt: skip
        assert caddy.returncode == 0, caddy.stdout + caddy.stderr


@pytest.mark.docker
@pytest.mark.skipif(not docker_available(), reason="docker is not available")
def test_compose_reads_password_literally(tmp_path: Path) -> None:
    out = tmp_path / "ls"
    answers = ["1", "8000", "./data", "Europe/Amsterdam", TRICKY_PW, TRICKY_PW]
    ok(run_install(out, answers))
    config = compose_config(out)
    assert config.returncode == 0, config.stderr
    environment = json.loads(config.stdout)["services"]["linersync"]["environment"]
    # `config` re-escapes every `$` as `$$` in its output so it stays loadable;
    # an interpolated value would have lost a `$` before that.
    assert environment["LINERSYNC_ADMIN_PASSWORD"] == TRICKY_PW.replace("$", "$$")


@pytest.mark.parametrize("folder", [".", "./", "./.", ".//"])
def test_refuses_the_setup_folder_as_data_folder(tmp_path: Path, folder: str) -> None:
    out = tmp_path / "ls"
    answers = ["1", "8000", folder, "./data", "Europe/Amsterdam", PW, PW]
    result = ok(run_install(out, answers))
    assert "Choose another folder name." in result.stdout
    assert '"./data:/data"' in (out / "docker-compose.yml").read_text()


def test_password_generation_failure_stops(tmp_path: Path) -> None:
    fake = tmp_path / "bin"
    fake.mkdir()
    head = fake / "head"
    head.write_text("#!/bin/sh\nexit 1\n")
    head.chmod(0o755)
    out = tmp_path / "ls"
    path = f"{fake}{os.pathsep}{os.environ['PATH']}"
    result = run_install(
        out, ["1", "8000", "./data", "Europe/Amsterdam", ""], extra_env={"PATH": path}
    )
    assert result.returncode == 1
    assert "couldn't generate a password" in result.stderr
    assert not out.exists()


def test_own_proxy_prints_docker_proxy_note(tmp_path: Path) -> None:
    stdout = ok(run_install(tmp_path / "ls", OWN_PROXY)).stdout
    assert "If your proxy runs in a Docker container" in stdout
    assert "linersync:8000" in stdout


def test_rerun_away_from_bundled_proxy_removes_orphans(tmp_path: Path) -> None:
    out = tmp_path / "ls"
    first = ok(run_install(out, BUNDLED_LE)).stdout
    assert "docker compose up -d --remove-orphans" not in first
    again = ok(run_install(out, ["y", *SAME_MACHINE])).stdout
    assert "&& docker compose up -d --remove-orphans" in again
    plain = ok(run_install(tmp_path / "other", SAME_MACHINE)).stdout
    assert "--remove-orphans" not in plain


def test_warns_when_running_as_root(tmp_path: Path) -> None:
    result = ok(
        run_install(tmp_path / "ls", SAME_MACHINE, extra_env={"LINERSYNC_UID": "0"})
    )
    assert "will run as root" in result.stderr
    assert (
        "will run as root" not in ok(run_install(tmp_path / "b", SAME_MACHINE)).stderr
    )
