from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient

from linersync.adapters.connectors.kit.plugin import ConnectionTest, ConnectorContext
from linersync.adapters.fake.connector import FakeConnector
from linersync.adapters.fake.plugin import FakePlugin
from linersync.adapters.memory.runtime import FixedClock
from linersync.domain.jobs import BackupJob, JobStatus, SyncJob
from linersync.domain.runs import Trigger
from linersync.domain.tracks import ServiceId, TrackKey
from linersync.ports.quota import QuotaPolicy
from linersync.ports.repositories import BreakerState
from tests.factories import snapshot, track
from tests.server.conftest import START, AppFactory, drain, portal, services
from tests.server.helpers import FULL, World, connect, create, new_sync, run_now, world
from tests.server.plugins import registry


class TunedPlugin(FakePlugin):
    """A FakePlugin with a fixed login expiry, quota policy and test result."""

    def __init__(
        self,
        connector: FakeConnector,
        *,
        expires: datetime | None = None,
        policy: QuotaPolicy | None = None,
        test_message: str | None = None,
        estimated: bool = False,
    ) -> None:
        super().__init__(connector)
        self.expires = expires
        self.login_expiry_estimated = estimated
        self.quota_policy = policy or QuotaPolicy()
        self.test_message = test_message

    def login_expires_at(self, credentials: Mapping[str, str]) -> datetime | None:
        return self.expires

    async def test_connection(self, ctx: ConnectorContext) -> ConnectionTest:
        if self.test_message is not None:
            return ConnectionTest(False, self.test_message)
        return await super().test_connection(ctx)


def attention(client: TestClient) -> list[dict[str, Any]]:
    response = client.get("/api/attention")
    assert response.status_code == 200, response.text
    items: list[dict[str, Any]] = response.json()
    return items


def act(client: TestClient, item_id: str, action: str, **body: Any) -> Any:
    return client.post(f"/api/attention/{item_id}/{action}", json=body)


@contextmanager
def unmatched_world(app_factory: AppFactory) -> Iterator[tuple[World, str]]:
    """A real run left "Dup song" unmatched with candidates D1 and D2."""
    with world(app_factory) as w:
        w.a.add_playlist(
            snapshot(
                "fake_a",
                "P1",
                ["a", "dup"],
                tracks={"dup": track("fake_a", "dup", title="Dup song")},
            )
        )
        w.b.catalog = (
            *w.b.catalog,
            track("fake_b", "D1", title="Dup song"),
            track("fake_b", "D2", title="Dup song"),
        )
        sync_id = create(w.client)["sync_id"]
        run_now(w.client, sync_id)
        drain(w.client)
        yield w, sync_id


def remembered(client: TestClient) -> str | None:
    match = services(client).repos.matches.get(
        TrackKey(ServiceId("fake_a"), "dup"), ServiceId("fake_b")
    )
    assert match is not None
    return match.target_track_id


def test_pick_candidate_remembers_and_resolves(app_factory: AppFactory) -> None:
    with unmatched_world(app_factory) as (w, sync_id):
        [item] = attention(w.client)
        response = act(w.client, item["item_id"], "pick", target_track_id="D2")
        assert response.status_code == 204, response.text
        assert attention(w.client) == []
        target = remembered(w.client)
        run_now(w.client, sync_id, dry_run=True)
        drain(w.client)
        run_id = w.client.get(f"/api/syncs/{sync_id}").json()["last_run"]["run_id"]
        run = w.client.get(f"/api/runs/{run_id}").json()
    assert item["kind"] == "unmatched"
    assert (item["sync_id"], item["sync_name"]) == (sync_id, "Mix")
    assert (item["member_id"], item["service"]) == ("dst0", "fake_b")
    assert item["target_service"] == "fake_b"
    assert item["source"]["title"] == "Dup song"
    assert item["source"]["known"] is True
    assert sorted(c["track"]["track_id"] for c in item["candidates"]) == ["D1", "D2"]
    assert item["not_made"] == []
    assert isinstance(item["detail"], str)
    assert target == "D2"
    assert run["dry_run"] is True
    assert run["unresolved"] == []


def test_similar_finds_looser_matches_that_can_be_picked(
    app_factory: AppFactory,
) -> None:
    with world(app_factory) as w:
        w.a.add_playlist(
            snapshot(
                "fake_a",
                "P1",
                ["a", "rm"],
                tracks={"rm": track("fake_a", "rm", title="Hidden song (Remastered)")},
            )
        )
        w.b.catalog = (
            *w.b.catalog,
            track("fake_b", "H1", title="Hidden song", artists=("Other",)),
        )
        run_now(w.client, create(w.client)["sync_id"])
        drain(w.client)
        [item] = [i for i in attention(w.client) if i["kind"] == "unmatched"]
        response = act(w.client, item["item_id"], "similar")
        assert response.status_code == 200, response.text
        found = response.json()
        picked = act(w.client, item["item_id"], "pick", target_track_id="H1")
        again = act(w.client, item["item_id"], "similar")
    assert item["candidates"] == []
    assert [c["track"]["track_id"] for c in found["candidates"]] == ["H1"]
    assert found["candidates"][0]["confidence"] >= 0.5
    assert picked.status_code == 204, picked.text
    assert again.status_code == 409


def test_pick_must_be_a_candidate(app_factory: AppFactory) -> None:
    with unmatched_world(app_factory) as (w, _):
        [item] = attention(w.client)
        response = act(w.client, item["item_id"], "pick", target_track_id="A")
        assert response.status_code == 422
        assert act(w.client, "nope", "pick", target_track_id="D1").status_code == 404
        assert attention(w.client) == [item]


def test_not_on_service(app_factory: AppFactory) -> None:
    with unmatched_world(app_factory) as (w, _):
        [item] = attention(w.client)
        response = act(w.client, item["item_id"], "not-on-service")
        assert response.status_code == 204
        assert response.content == b""
        assert attention(w.client) == []
        assert remembered(w.client) is None


def test_approve_runs_braked_sync(app_factory: AppFactory) -> None:
    songs = ("a", "b", "c", "d")
    with world(app_factory, source=songs, catalog=songs) as w:
        sync_id = create(w.client)["sync_id"]
        run_now(w.client, sync_id)
        drain(w.client)
        w.a.add_playlist(snapshot("fake_a", "P1", ["a"]))
        svc = services(w.client)
        svc.jobs.submit(SyncJob(sync_id, Trigger.SCHEDULED), "Sync “Mix”")
        drain(w.client)
        held = [e.track.track_id for e in w.b.playlists["B1"].entries]
        [item] = attention(w.client)
        response = act(w.client, item["item_id"], "approve", run_now=True)
        assert response.status_code == 200, response.text
        job = response.json()["job"]
        stored = svc.job_store.get(job["job_id"])
        drain(w.client)
        after = [e.track.track_id for e in w.b.playlists["B1"].entries]
        left = attention(w.client)
    assert held == ["A", "B", "C", "D"]
    assert (item["kind"], item["sync_name"], item["member_id"]) == (
        "brake",
        "Mix",
        "dst0",
    )
    assert item["detail"] == "This run would remove 3 of 4 songs"
    assert (job["kind"], job["sync_id"], job["status"]) == ("sync", sync_id, "queued")
    assert stored is not None and stored.request == SyncJob(sync_id, Trigger.MANUAL)
    assert after == ["A"]
    assert left == []


def test_resume_paused_source(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        sync_id = create(w.client)["sync_id"]
        w.a.drop_playlist("P1")
        run_now(w.client, sync_id)
        drain(w.client)
        paused = w.client.get(f"/api/syncs/{sync_id}").json()
        [item] = attention(w.client)
        w.a.add_playlist(snapshot("fake_a", "P1", ["a"]))
        response = act(w.client, item["item_id"], "resume", run_now=False)
        resumed = w.client.get(f"/api/syncs/{sync_id}").json()
    assert paused["status"] == "paused"
    assert paused["next_run_at"] is None
    assert item["kind"] == "source_missing"
    assert response.status_code == 200, response.text
    assert response.json() == {"job": None}
    assert (resumed["status"], resumed["pause_reason"]) == ("active", None)
    assert resumed["next_run_at"] is not None
    assert resumed["active_job"] is None


def test_resolving_twice_409(app_factory: AppFactory) -> None:
    with unmatched_world(app_factory) as (w, _):
        [item] = attention(w.client)
        item_id = item["item_id"]
        wrong_kind = act(w.client, item_id, "approve", run_now=True)
        first = act(w.client, item_id, "dismiss")
        second = act(w.client, item_id, "dismiss")
        again = act(w.client, item_id, "pick", target_track_id="D1")
        unknown = act(w.client, "nope", "dismiss")
    assert wrong_kind.status_code == 409
    assert first.status_code == 204
    assert second.status_code == again.status_code == 409
    assert isinstance(second.json()["detail"], str)
    assert unknown.status_code == 404


def test_attention_requires_session(app_factory: AppFactory) -> None:
    with TestClient(app_factory()) as client:
        assert client.get("/api/attention").status_code == 401
        assert act(client, "x", "dismiss").status_code == 401
        assert client.get("/api/home").status_code == 401
        assert client.get("/api/settings").status_code == 401


def test_home_panels(app_factory: AppFactory, clock: FixedClock) -> None:
    with world(app_factory, source=("a", "b", "c", "zzz")) as w:
        done = create(w.client, name="Done")["sync_id"]
        run_now(w.client, done)
        drain(w.client)
        busy = create(w.client, name="Busy")["sync_id"]
        waiting = create(w.client, name="Waiting")["sync_id"]
        store = services(w.client).job_store
        clock.advance(timedelta(seconds=1))
        busy_job = run_now(w.client, busy).json()
        stored = store.get(busy_job["job_id"])
        assert stored is not None
        store.save(replace(stored, status=JobStatus.RUNNING, started_at=clock.now()))
        clock.advance(timedelta(seconds=1))
        waiting_job = run_now(w.client, waiting).json()
        response = w.client.get("/api/home")
        syncs = {s["name"]: s for s in w.client.get("/api/syncs").json()}
        extra = [new_sync(f"Extra {i}") for i in range(9)]
        assert (
            w.client.post("/api/syncs/bulk", json={"syncs": extra}).status_code == 201
        )
        many = w.client.get("/api/home").json()
    assert response.status_code == 200, response.text
    home = response.json()
    assert [j["job_id"] for j in home["running"]] == [busy_job["job_id"]]
    assert home["running"][0]["status"] == "running"
    assert [j["job_id"] for j in home["queued"]] == [waiting_job["job_id"]]
    [recent] = home["recent"]
    assert (recent["sync_id"], recent["status"]) == (done, "succeeded")
    assert home["attention_count"] == 1
    assert home["warnings"] == []
    next_up = [n for n in home["next_up"] if n["kind"] == "sync"]
    assert sorted(n["name"] for n in next_up) == ["Busy", "Done", "Waiting"]
    for n in next_up:
        assert n["sync_id"] == syncs[n["name"]]["sync_id"]
        assert n["at"] == syncs[n["name"]]["next_run_at"]
    ats = [datetime.fromisoformat(n["at"]) for n in many["next_up"]]
    assert len(ats) == 10
    assert ats == sorted(ats)


def test_estimated_login_expiry_reads_as_a_reminder(app_factory: AppFactory) -> None:
    def make(connector: FakeConnector) -> FakePlugin:
        expires = START + timedelta(days=10)
        return TunedPlugin(connector, expires=expires, estimated=True)

    with world(app_factory, make_plugin=make) as w:
        warnings = w.client.get("/api/home").json()["warnings"]
        info = w.client.get("/api/connections/fake_a").json()
    assert warnings[0] == {
        "service": "fake_a",
        "kind": "login_expiring",
        "message": "Fake A may sign you out around 11 Jan 2026; "
        "sign in again before then to be safe",
    }
    assert info["login_expiry_estimated"] is True


def test_home_warnings(app_factory: AppFactory) -> None:
    def make(connector: FakeConnector) -> FakePlugin:
        if connector.service == "fake_a":
            return TunedPlugin(
                connector, expires=START + timedelta(days=10), test_message="Bad token"
            )
        return TunedPlugin(connector, expires=START + timedelta(days=20))

    with world(app_factory, make_plugin=make) as w:
        assert w.client.get("/api/home").json()["warnings"] == [
            {
                "service": "fake_a",
                "kind": "login_expiring",
                "message": "Fake A sign-in expires on 11 Jan 2026",
            }
        ]
        assert w.client.post("/api/connections/fake_a/test", json={}).status_code == 200
        svc = services(w.client)
        svc.connections.on_auth_required(ServiceId("fake_b"))
        svc.repos.breakers.save(BreakerState(ServiceId("fake_b"), 5, START, "boom"))
        warnings = w.client.get("/api/home").json()["warnings"]
    assert warnings == [
        {"service": "fake_a", "kind": "failing", "message": "Bad token"},
        {
            "service": "fake_a",
            "kind": "login_expiring",
            "message": "Fake A sign-in expires on 11 Jan 2026",
        },
        {
            "service": "fake_b",
            "kind": "needs_sign_in",
            "message": "Sign in to Fake B again",
        },
        {
            "service": "fake_b",
            "kind": "service_paused",
            "message": "Fake B keeps failing; its syncs are paused until 00:30",
        },
    ]


def test_api_limits_use_quota_day(app_factory: AppFactory, clock: FixedClock) -> None:
    policy = QuotaPolicy(100, "America/Los_Angeles")
    with world(
        app_factory,
        make_plugin=lambda c: TunedPlugin(c, policy=policy),
        connect_b=True,
    ) as w:
        clock.advance(timedelta(hours=12))  # 04:00 on 1 Jan in Los Angeles
        quota = services(w.client).quota
        b = ServiceId("fake_b")
        quota.record(b, 7, datetime(2026, 1, 1, 7, tzinfo=UTC))  # 31 Dec there
        quota.record(b, 5, datetime(2026, 1, 1, 9, tzinfo=UTC))
        limits = w.client.get("/api/home").json()["api_limits"]
    resets = datetime(2026, 1, 2, 8, tzinfo=UTC)
    assert [
        (x["service"], x["display_name"], x["used"], x["limit"]) for x in limits
    ] == [("fake_a", "Fake A", 0, 100), ("fake_b", "Fake B", 5, 100)]
    assert all(datetime.fromisoformat(x["resets_at"]) == resets for x in limits)


DEFAULTS = {
    "default_schedule": "0 */6 * * *",
    "timezone": "UTC",
    "brake_max_fraction": 0.2,
    "brake_max_count": 50,
    "match_accept_threshold": 0.85,
    "match_min_margin": 0.05,
    "breaker_failure_threshold": 5,
    "breaker_cooldown_minutes": 30,
    "backup_schedule": "0 3 * * *",
    "backup_versions_kept": 5,
}

CHANGED = {
    "default_schedule": "15 1 * * *",
    "brake_max_fraction": 0.5,
    "brake_max_count": 0,
    "match_accept_threshold": 1.0,
    "match_min_margin": 0.0,
    "breaker_failure_threshold": 1,
    "breaker_cooldown_minutes": 90,
    "backup_schedule": "30 2 * * 1",
    "backup_versions_kept": 12,
}


def test_settings_roundtrip_and_validation(signed_in: TestClient) -> None:
    client = signed_in
    assert client.get("/api/settings").json() == DEFAULTS
    response = client.put("/api/settings", json=CHANGED)
    assert response.status_code == 200, response.text
    assert response.json() == {**CHANGED, "timezone": "UTC"}
    assert client.get("/api/settings").json() == response.json()
    engine = services(client).settings_store.get().engine
    assert engine.match.max_candidates == 5
    assert engine.breaker.cooldown == timedelta(minutes=90)
    bad_values: list[tuple[str, Any]] = [
        ("default_schedule", "every day"),
        ("brake_max_fraction", 0),
        ("brake_max_fraction", 1.5),
        ("brake_max_count", -1),
        ("match_accept_threshold", 1.1),
        ("match_min_margin", -0.1),
        ("breaker_failure_threshold", 0),
        ("breaker_cooldown_minutes", 0),
        ("backup_schedule", "nope"),
        ("backup_versions_kept", 0),
        ("backup_versions_kept", 101),
    ]
    for key, value in bad_values:
        bad = client.put("/api/settings", json={**CHANGED, key: value})
        assert bad.status_code == 422, (key, value)
    missing = {k: v for k, v in CHANGED.items() if k != "brake_max_count"}
    assert client.put("/api/settings", json=missing).status_code == 422
    assert client.get("/api/settings").json() == {**CHANGED, "timezone": "UTC"}


def test_settings_change_reschedules(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        default = create(w.client, name="Default")["sync_id"]
        custom = create(w.client, name="Custom", schedule="45 4 * * *")["sync_id"]
        response = w.client.put("/api/settings", json=CHANGED)
        assert response.status_code == 200
        moved = w.client.get(f"/api/syncs/{default}").json()
        kept = w.client.get(f"/api/syncs/{custom}").json()
    at = datetime.fromisoformat(moved["next_run_at"])
    assert (at.hour, at.minute) == (1, 15)
    assert moved["effective_schedule"] == "15 1 * * *"
    kept_at = datetime.fromisoformat(kept["next_run_at"])
    assert (kept_at.hour, kept_at.minute) == (4, 45)


def _settings_body(client: TestClient, **changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = client.get("/api/settings").json()
    del body["timezone"]
    return {**body, **changes}


def test_settings_include_backup_fields(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        got = w.client.get("/api/settings").json()
        before = w.client.get("/api/home").json()["next_up"]
        off = w.client.put(
            "/api/settings", json=_settings_body(w.client, backup_schedule=None)
        )
        after = w.client.get("/api/home").json()["next_up"]
        bad = w.client.put(
            "/api/settings", json=_settings_body(w.client, backup_schedule="nope")
        )
    assert (got["backup_schedule"], got["backup_versions_kept"]) == ("0 3 * * *", 5)
    assert [n["kind"] for n in before] == ["backup"]
    assert off.status_code == 200, off.text
    assert off.json()["backup_schedule"] is None
    assert after == []
    assert bad.status_code == 422


def test_next_up_lists_backup(app_factory: AppFactory) -> None:
    with world(app_factory) as w:
        sync_id = create(w.client, name="Mix")["sync_id"]
        next_up = w.client.get("/api/home").json()["next_up"]
    by_kind = {n["kind"]: n for n in next_up}
    assert set(by_kind) == {"sync", "backup"}
    assert by_kind["backup"]["name"] == "Back up all services"
    assert by_kind["backup"]["sync_id"] is None
    assert (by_kind["sync"]["sync_id"], by_kind["sync"]["name"]) == (sync_id, "Mix")
    ats = [datetime.fromisoformat(n["at"]) for n in next_up]
    assert ats == sorted(ats)


def _fire_backup(client: TestClient) -> None:
    async def fire() -> None:
        job = services(client).scheduler.aps.get_job("backup")
        assert job is not None
        await job.func()

    portal(client).call(fire)


def test_scheduled_backup_skipped_without_services(app_factory: AppFactory) -> None:
    app = app_factory(
        registry=registry(
            FakePlugin(FakeConnector(ServiceId("fake_a"), "Fake A", FULL))
        )
    )
    with TestClient(app) as client:
        assert (
            client.post("/api/auth/login", json={"password": "pw"}).status_code == 204
        )
        _fire_backup(client)
        none = services(client).job_store.list_by_status(JobStatus.QUEUED)
        connect(client, "fake_a")
        _fire_backup(client)
        queued = services(client).job_store.list_by_status(JobStatus.QUEUED)
    assert none == []
    [job] = queued
    assert job.request == BackupJob((), Trigger.SCHEDULED)
    assert job.label == "Back up all services"
