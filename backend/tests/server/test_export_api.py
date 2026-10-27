import base64
import hashlib
import io
import json
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from linersync.adapters.fake.connector import FakeConnector
from linersync.adapters.fake.plugin import FakePlugin
from linersync.adapters.memory.backups import StaticCovers
from linersync.domain.backups import CoverImage
from linersync.domain.tracks import ServiceId
from linersync.server.api import export as export_api
from tests.factories import snapshot
from tests.server.conftest import AppFactory, services
from tests.server.helpers import (
    FULL,
    World,
    archive_p2,
    back_up,
    create,
    latest_version,
    world,
)
from tests.server.plugins import registry

COVER_URL = "https://img.example/p1.png"
COVER_DATA = b"\x89PNG-export"
COVER = CoverImage(hashlib.sha256(COVER_DATA).hexdigest(), "image/png", COVER_DATA)
JSON = {"Content-Type": "application/json"}


def test_version_csv_download(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        entry = archive_p2(w)
        vid = latest_version(w.client, entry["entry_id"])
        r = w.client.get(f"/api/backups/versions/{vid}/csv")
        assert r.status_code == 200
        assert r.headers["content-type"] == "text/csv; charset=utf-8"
        assert "filename*=" in r.headers["content-disposition"]
        assert "Road trip (fake_a).csv" in r.headers["content-disposition"]
        assert r.content.startswith(b"\xef\xbb\xbf")


def test_csv_has_bom_and_crlf(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        entry = archive_p2(w)
        r = w.client.get("/api/export/csv", params={"entry_id": entry["entry_id"]})
        assert r.status_code == 200
        first = r.content.split(b"\r\n")[0]
        assert first.startswith(b"\xef\xbb\xbfposition,title")
        assert b"\r\n" in r.content


def test_export_csv_one_entry_is_csv(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        entry = archive_p2(w)
        r = w.client.get("/api/export/csv", params={"entry_id": entry["entry_id"]})
        assert r.headers["content-type"].startswith("text/csv")
        text = r.content.decode("utf-8-sig")
        assert text.count("\r\n") == 3  # header + songs a, b


def test_export_csv_many_is_zip_with_archive_folder(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        archive_p2(w)
        r = w.client.get("/api/export/csv")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/zip"
        disp = r.headers["content-disposition"]
        assert "linersync-csv-20260101-000000.zip" in disp
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            names = z.namelist()
        assert "archive/Road trip (fake_a).csv" in names
        assert "Mix (fake_a).csv" in names
        assert "Mix (fake_b).csv" in names or any(n.startswith("Mix") for n in names)


def test_export_csv_unknown_entry_404(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        back_up(w.client)
        r = w.client.get("/api/export/csv", params={"entry_id": "nope"})
        assert r.status_code == 404
        assert r.json() == {"detail": "Unknown backup"}


def test_export_csv_nothing_backed_up_404(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        r = w.client.get("/api/export/csv")
        assert r.status_code == 404
        assert r.json() == {"detail": "Nothing has been backed up yet"}
        assert ServiceId("x")


# -- JSON export and import ---------------------------------------------------
@contextmanager
def exported_world(app_factory: AppFactory) -> Iterator[tuple[World, Any]]:
    """A world with a sync and a backup (P1 has a cover); yields it and the
    downloaded export text."""
    with world(app_factory, covers=StaticCovers({COVER_URL: COVER})) as w:
        p1 = snapshot("fake_a", "P1", ["a", "b", "c"])
        w.a.add_playlist(replace(p1, meta=replace(p1.meta, cover_url=COVER_URL)))
        create(w.client)
        back_up(w.client)
        r = w.client.get("/api/export")
        assert r.status_code == 200, r.text
        yield w, r.text


@contextmanager
def other_install(app_factory: AppFactory, tmp_path: Path) -> Iterator[TestClient]:
    a = FakeConnector(ServiceId("fake_a"), "Fake A", FULL)
    b = FakeConnector(ServiceId("fake_b"), "Fake B", FULL)
    app = app_factory(
        data_dir=tmp_path / "other", registry=registry(FakePlugin(a), FakePlugin(b))
    )
    with TestClient(app) as client:
        assert (
            client.post("/api/auth/login", json={"password": "pw"}).status_code == 204
        )
        yield client


def _import(client: TestClient, body: str | bytes) -> Any:
    return client.post("/api/import", content=body, headers=JSON)


def test_export_download(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        r = w.client.get("/api/export")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/json"
        disp = r.headers["content-disposition"]
        assert "linersync-export-20260101-000000.json" in disp
        assert r.json()["format"] == "linersync-export"
        assert r.json()["version"] == 1


def test_export_then_import_into_new_install(
    app_factory: AppFactory, tmp_path: Path
) -> None:
    with (
        exported_world(app_factory) as (_, text),
        other_install(app_factory, tmp_path) as other,
    ):
        r = _import(other, text)
        assert r.status_code == 200, r.text
        report = r.json()
        assert report["added"]["syncs"] == 1
        assert report["added"]["backup_playlists"] == 3  # P1, P2 and B1
        assert report["settings_applied"] is True
        syncs = other.get("/api/syncs").json()
        assert len(syncs) == 1
        assert syncs[0]["status"] == "paused"
        assert syncs[0]["pause_reason"] == (
            "Imported from an export; check it, then resume it"
        )
        assert syncs[0]["next_run_at"] is None
        entries = other.get("/api/backups/playlists").json()
        assert sorted(e["playlist_id"] for e in entries) == ["B1", "P1", "P2"]
        p1 = next(e for e in entries if e["playlist_id"] == "P1")
        assert p1["versions"] == 1
        cover = other.get(p1["cover_url"])
        assert cover.status_code == 200
        assert cover.content == COVER_DATA
        again = _import(other, text).json()
        assert again["added"]["syncs"] == 0
        assert again["skipped"]["syncs"] == 1


def test_imported_syncs_are_paused_and_unscheduled(
    app_factory: AppFactory, tmp_path: Path
) -> None:
    with (
        exported_world(app_factory) as (_, text),
        other_install(app_factory, tmp_path) as other,
    ):
        sync_id = json.loads(text)["syncs"][0]["sync_id"]
        assert _import(other, text).status_code == 200
        assert sync_id not in services(other).scheduler.next_runs()


def test_export_leaves_out_secrets(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        w.client.put(
            "/api/connections/fake_a/credentials",
            json={"values": {"account": "top-secret-account"}},
        )
        text = w.client.get("/api/export").text
        assert "top-secret-account" not in text
        assert "credentials" not in text


def test_import_rejects_bad_files(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        r = _import(w.client, json.dumps({"hello": 1}))
        assert r.status_code == 422
        assert r.json() == {"detail": "This file isn't a LinerSync export"}
        r = _import(w.client, json.dumps({"format": "linersync-export", "version": 2}))
        assert r.status_code == 422
        assert "newer version" in r.json()["detail"]
        damaged = {
            "format": "linersync-export",
            "version": 1,
            "exported_at": "2026-01-01T00:00:00+00:00",
            "app_version": "0.1.0",
            "settings": {},
            "syncs": [{"bad": 1}],
        }
        r = _import(w.client, json.dumps(damaged))
        assert r.status_code == 422
        assert r.json()["detail"].startswith("The export file is damaged:")
        assert w.client.get("/api/syncs").json() == []
        r = _import(w.client, b"not json at all {")
        assert r.status_code == 422
        assert r.json() == {"detail": "This file isn't a LinerSync export"}


def test_import_too_large(
    app_factory: AppFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(export_api, "MAX_IMPORT_BYTES", 10)
    with world(app_factory) as w:
        r = _import(w.client, json.dumps({"format": "linersync-export"}))
        assert r.status_code == 413
        assert r.json() == {"detail": "The file is too large"}


def test_import_locked_while_backup_queued(app_factory: AppFactory) -> None:
    with exported_world(app_factory) as (w, text):
        assert w.client.post("/api/backups/run", json={}).status_code == 202
        r = _import(w.client, text)
        assert r.status_code == 409
        assert r.json() == {"detail": "Wait for the current backup to finish"}


# -- type damage and cover checks (nothing written) ---------------------------
GOLDEN = Path(__file__).parent.parent / "fixtures/export/export_v1.json"
GOOD_COVER = b"\xff\xd8real-jpeg"


def _golden(**cover: str) -> dict[str, Any]:
    """The golden export, with its cover made self-consistent (the fixture's
    sha is a placeholder) unless overridden."""
    data: dict[str, Any] = json.loads(GOLDEN.read_text(encoding="utf-8"))
    sha = hashlib.sha256(GOOD_COVER).hexdigest()
    data["covers"] = [
        {
            "sha256": sha,
            "media_type": "image/jpeg",
            "data_base64": base64.b64encode(GOOD_COVER).decode("ascii"),
        }
        | cover
    ]
    for item in (*data["backup_versions"], *data["backup_playlists"]):
        if item.get("cover_sha256") is not None:
            item["cover_sha256"] = sha
    return data


def _assert_damaged_and_nothing_written(client: TestClient, data: Any) -> None:
    syncs = client.get("/api/syncs").json()
    playlists = client.get("/api/backups/playlists").json()
    r = _import(client, json.dumps(data))
    assert r.status_code == 422, r.text
    assert r.json()["detail"].startswith("The export file is damaged:")
    assert client.get("/api/syncs").json() == syncs
    assert client.get("/api/backups/playlists").json() == playlists


def test_golden_with_consistent_cover_imports(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        r = _import(w.client, json.dumps(_golden()))
        assert r.status_code == 200, r.text
        assert r.json()["added"]["backup_playlists"] == 2


def test_import_rejects_null_name(app_factory: AppFactory) -> None:
    data = _golden()
    data["backup_playlists"][0]["name"] = None
    with world(app_factory) as w:
        _assert_damaged_and_nothing_written(w.client, data)
        assert w.client.get("/api/backups/playlists").status_code == 200


def test_import_rejects_list_where_value_expected(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        data = _golden()
        data["syncs"][0]["sync_id"] = [1]
        _assert_damaged_and_nothing_written(w.client, data)
        data = _golden()
        data["syncs"][0]["members"][0] = None
        _assert_damaged_and_nothing_written(w.client, data)
        data = _golden()
        data["backup_runs"][0]["errors"] = {"a": 1}
        _assert_damaged_and_nothing_written(w.client, data)


def test_import_rejects_cover_with_wrong_sha(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        _assert_damaged_and_nothing_written(w.client, _golden(sha256="c0ffee"))


@pytest.mark.parametrize("media_type", ["text/html", "image/svg+xml"])
def test_import_rejects_cover_media_type(
    app_factory: AppFactory, media_type: str
) -> None:
    with world(app_factory) as w:
        _assert_damaged_and_nothing_written(w.client, _golden(media_type=media_type))
