from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.server.conftest import AppFactory

INDEX = '<!doctype html><div id="root"></div>'


@pytest.fixture
def web(tmp_path: Path) -> Path:
    folder = tmp_path / "web"
    (folder / "assets").mkdir(parents=True)
    (folder / "index.html").write_text(INDEX)
    (folder / "favicon.svg").write_text("<svg/>")
    (folder / "assets" / "app-abc123.js").write_text("console.log(1)")
    return folder


@pytest.fixture
def web_client(
    app_factory: AppFactory, web: Path, tmp_path: Path
) -> Iterator[TestClient]:
    with TestClient(app_factory(web_dir=web, data_dir=tmp_path / "data")) as c:
        yield c


def test_root_serves_index_without_session(web_client: TestClient) -> None:
    r = web_client.get("/")
    assert r.status_code == 200
    assert '<div id="root">' in r.text
    assert r.headers["cache-control"] == "no-cache"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"


@pytest.mark.parametrize("path", ["/syncs/abc", "/backup/playlists/x"])
def test_deep_link_serves_index(web_client: TestClient, path: str) -> None:
    r = web_client.get(path)
    assert r.status_code == 200
    assert r.text == INDEX


def test_hashed_asset_is_immutable(web_client: TestClient) -> None:
    r = web_client.get("/assets/app-abc123.js")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert web_client.get("/favicon.svg").headers["cache-control"] == "no-cache"


def test_missing_file_is_404_not_index(web_client: TestClient) -> None:
    r = web_client.get("/assets/app-old999.js")
    assert r.status_code == 404
    assert INDEX not in r.text


@pytest.mark.parametrize("path", ["/api/nope", "/api"])
def test_unknown_api_path_is_json_404(web_client: TestClient, path: str) -> None:
    r = web_client.get(path)
    assert r.status_code == 404
    assert r.json() == {"detail": "Not Found"}
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"


@pytest.mark.parametrize("path", ["/..%2Fsecret.txt", "/assets/..%2F..%2Fsecret.txt"])
def test_path_traversal_is_refused(
    web_client: TestClient, tmp_path: Path, path: str
) -> None:
    (tmp_path / "secret.txt").write_text("top-secret")
    r = web_client.get(path)
    assert "top-secret" not in r.text


def test_api_still_works(web_client: TestClient) -> None:
    r = web_client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_no_web_dir_no_route(app_factory: AppFactory) -> None:
    with TestClient(app_factory()) as c:
        assert c.get("/").status_code == 404


@pytest.mark.parametrize("path", ["/%00", "/a%00.js"])
def test_nul_byte_in_path_is_not_a_server_error(
    web_client: TestClient, path: str
) -> None:
    r = web_client.get(path)
    assert r.status_code < 500
    if path.endswith(".js"):
        assert r.status_code == 404
        assert INDEX not in r.text
