from pathlib import Path

from fastapi.testclient import TestClient

from tests.e2e_server import E2E_PASSWORD, build_app


def test_e2e_world(tmp_path: Path) -> None:
    with TestClient(build_app(tmp_path, "http://testserver")) as client:
        login = client.post("/api/auth/login", json={"password": E2E_PASSWORD})
        assert login.status_code == 204

        listed = client.get("/api/connections").json()["services"]
        by_service = {c["service"]: c for c in listed}
        assert {"fake_a", "fake_b", "fake_c"} <= set(by_service)
        for service in ("fake_a", "fake_b", "fake_c"):
            assert by_service[service]["status"] == "not_set_up"

        saved = client.put(
            "/api/connections/fake_a/credentials", json={"values": {"account": "me"}}
        )
        assert saved.status_code == 200
        assert client.post("/api/connections/fake_a/test", json={}).status_code == 200

        def names() -> list[str]:
            response = client.get("/api/connections/fake_a/playlists")
            assert response.status_code == 200
            return [p["name"] for p in response.json()]

        assert names() == ["Road Trip", "Focus", "Old Mix"]
        gone = client.post(
            "/api/e2e/delete-playlist",
            json={"service": "fake_a", "playlist_id": "P3"},
        )
        assert gone.status_code == 204
        assert "Old Mix" not in names()
