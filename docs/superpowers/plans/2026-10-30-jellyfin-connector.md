# Jellyfin Connector (+ read-only groundwork) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only Jellyfin connector, together with the shared groundwork it needs: read-only services can't be destinations, and the contract suite accepts connectors without ISRCs.

**Architecture:** A new `adapters/connectors/jellyfin/` package (`parse.py`, `connector.py`, `plugin.py`) talks to the user's Jellyfin REST API through the shared `kit/http.py` `HttpClient`, authenticated with an admin API key. It declares only `READ_ORDER` and is discovered through the `linersync.connectors` entry point. Destination validation moves into one server helper, `check_destinations`, which the frontend mirrors with a `writableOnly` filter on `ServiceSelect`.

**Tech Stack:** Python 3.13, httpx, FastAPI, pytest (asyncio auto); React + TypeScript + vitest.

**Spec:** `docs/superpowers/specs/2026-10-28-new-connectors-design.md` (sections "Jellyfin", "Cross-cutting changes" 1–3). This is plan 1 of 4; Tidal, YouTube Music and Qobuz each get their own plan.

## Global Constraints

- **No git commits.** The user commits manually. Each task ends with a "Checkpoint" step: list the changed files and stop for the user.
- No third-party Jellyfin SDK. All HTTP goes through `kit/http.py` `HttpClient`, built with `ctx.transport` and `ctx.sleep`.
- A failed read raises `ReadFailed` and never returns an empty or partial snapshot as complete. Raise `PlaylistNotFound` only on an authoritative 404.
- Auth is an admin API key, sent as `Authorization: MediaBrowser Token="<key>"`. Every per-user call passes `userId` explicitly.
- Jellyfin `RunTimeTicks` are 100 ns units: `duration_ms = ticks // 10_000`.
- Jellyfin tracks have no ISRC: `Track.isrc` is always `None`.
- Copy that must match exactly, in backend and frontend:
  - Limitation: `f"{name} is read-only, so it can't be a destination"`
  - Server 422 detail: `f"{display_name} is read-only, so it can't be a destination"`
- Commands, run from `backend/`: `uv run pytest`, `uv run mypy`, `uv run ruff check`. From `frontend/`: `npm test -- --run`, `npm run typecheck`, `npm run lint`.

## Review Focus

1. **Server URL entered with a trailing slash, a path prefix (`https://host/jellyfin`), or no scheme.** Expect: trailing slash stripped and path prefix kept; a missing scheme is rejected by the form pattern. Test: Task 5 `test_validate_normalises_server_url`.
2. **Username that matches no Jellyfin user, or differs only in case.** Expect: case-insensitive match; otherwise the connection test fails with `No Jellyfin user named "<name>"` and no exception. Test: Task 5 `test_connection_unknown_user`, `test_connection_matches_username_case_insensitively`.
3. **Revoked API key (401).** Expect: the connection test says `Check your Jellyfin API key` and reads raise `ReadFailed(auth_required=True)`. Test: Task 5 `test_connection_bad_key`, Task 4 `test_read_with_revoked_key_is_auth_required`.
4. **Playlist containing items that aren't audio (videos, or entries whose media file is gone).** Expect: skipped entries make the snapshot incomplete instead of silently disappearing. Test: Task 4 `test_non_audio_entry_makes_snapshot_incomplete`.
5. **Playlist changing during a paginated read.** Expect: `ReadFailed`, never a spliced snapshot. Test: Task 4 `test_total_changing_mid_read_fails`.

---

### Task 1: Contract suite accepts connectors without ISRCs

**Files:**
- Modify: `backend/src/linersync/testing/contract.py` (catalog comment at `:39-41`, `search_by_isrc_finds_track` at `:143-146`, `LAWS`)
- Test: `backend/tests/contracts/test_contract_laws.py` (create)

**Interfaces:**
- Produces: law `search_by_metadata_finds_track(h)`. `search_by_isrc_finds_track` raises `ContractSkip` when `h.catalog[0].isrc is None`. The catalog comment now reads "each with an ISRC if the service exposes them".

- [ ] **Step 1: Write the failing tests** using `FakeHarness` from `tests/contracts/harnesses.py`, with the catalog rebuilt so it has no ISRCs:

```python
async def test_isrc_law_skips_when_catalog_has_no_isrc() -> None:
    h = _no_isrc_fake_harness()
    with pytest.raises(ContractSkip):
        await LAWS["search_by_isrc_finds_track"](h)

async def test_metadata_law_runs_without_isrc() -> None:
    await LAWS["search_by_metadata_finds_track"](_no_isrc_fake_harness())
```

- [ ] **Step 2: Run** `uv run pytest tests/contracts/test_contract_laws.py -v`. Expected: FAIL (KeyError and no skip).
- [ ] **Step 3: Implement.** Add the skip to `search_by_isrc_finds_track`. Add `search_by_metadata_finds_track`: query `TrackQuery.from_track(target)` with `isrc=None` (via `dataclasses.replace`), and assert `target.track_id` is among the results. Register it in `LAWS`.
- [ ] **Step 4: Run** `uv run pytest tests/contracts -v`. Expected: PASS. The new law must pass for `fake`, `spotify` and `apple_music`. If a sim's search can't do metadata search, fix the sim, not the law.
- [ ] **Step 5: Checkpoint.** List changed files; stop for the user to commit.

### Task 2: Read-only services can't be destinations (backend)

**Files:**
- Modify: `backend/src/linersync/domain/capabilities.py` (`limitations`)
- Modify: `backend/src/linersync/server/api/syncs.py` (add `check_destinations` next to `check_services` at `:56`; use it at `:105` and `:284`)
- Modify: `backend/src/linersync/server/api/transfers.py:33,39`, `backend/src/linersync/server/api/backups.py:240` (restore destination)
- Test: `backend/tests/domain/test_capabilities.py` (add to it, or create it if missing), `backend/tests/server/test_syncs_api.py`, and the transfer/backup API tests that already cover `check_services` errors

**Interfaces:**
- Produces: `check_destinations(services: AppServices, names: Iterable[str]) -> None`. It runs `check_services` first, then raises a 422 with the read-only detail when the plugin's capabilities lack `Capability.ADD`.
- Produces: `limitations(...)` returns exactly `(f"{name} is read-only, so it can't be a destination",)` when `ADD` is missing. Add-only and other notes are not meaningful then.

- [ ] **Step 1: Write the failing tests.**

```python
def test_read_only_limitation() -> None:
    caps = frozenset({Capability.READ_ORDER})
    assert limitations("Jellyfin", caps, SyncMode.FULL) == (
        "Jellyfin is read-only, so it can't be a destination",
    )

def test_create_rejects_read_only_destination(app_factory: AppFactory) -> None:
    with world(app_factory, b_caps=frozenset({Capability.READ_ORDER})) as w:
        response = w.client.post("/api/syncs", json=new_sync())
        assert response.status_code == 422
        assert response.json() == {"detail": "Fake B is read-only, so it can't be a destination"}
```

Add the same 422 assertion for adding a destination with PATCH `/api/syncs/{id}`, for `POST /api/transfers`, for `/api/transfers/bulk`, and for the backup restore route. Also assert that a read-only service still works as a **source** (`world(..., source caps read-only)` creates the sync with 201).

- [ ] **Step 2: Run** `uv run pytest tests/domain tests/server -k "read_only" -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `check_destinations` and the `limitations` branch, and swap the four destination call sites. Leave `check_source` and `backups.py:106` on `check_services`.
- [ ] **Step 4: Run** `uv run pytest tests/domain tests/server -v`. Expected: PASS.
- [ ] **Step 5: Checkpoint.**

### Task 3: Read-only services can't be destinations (frontend)

**Files:**
- Modify: `frontend/src/features/pickers/ServiceSelect.tsx` (new prop `writableOnly?: boolean`)
- Modify: the destination pickers: `frontend/src/features/new-sync/NewSyncPage.tsx:69`, `frontend/src/features/transfers/NewTransferPage.tsx:143`, `frontend/src/features/syncs/AddDestination.tsx:36`, `frontend/src/features/backup/RestoreDialog.tsx:51`. Pass `writableOnly`. The source picker at `NewSyncPage.tsx:251` does not.
- Modify: `frontend/src/lib/limitations.ts` (mirror Task 2's branch)
- Modify: `frontend/src/test/fixtures.ts` `connectionOut` default `capabilities`: change from `[]` to all seven capability strings, so existing tests keep their destinations.
- Test: `frontend/src/features/pickers/PlaylistPicker.test.tsx` (it already renders `ServiceSelect`), `frontend/src/lib/limitations.test.ts` (create if missing)

- [ ] **Step 1: Write the failing tests.** With connections `spotify` (capabilities include `"add"`) and `jellyfin` (`["read_order"]`), a `<ServiceSelect writableOnly …/>` lists Spotify and not Jellyfin, and without `writableOnly` it lists both. Separately, `limitations("Jellyfin", ["read_order"], "full")` equals `["Jellyfin is read-only, so it can't be a destination"]`.
- [ ] **Step 2: Run** `npm test -- --run src/features/pickers src/lib`. Expected: FAIL.
- [ ] **Step 3: Implement** the filter `!props.writableOnly || s.capabilities.includes("add")` and the limitations branch, then pass `writableOnly` at the four destination sites.
- [ ] **Step 4: Run** `npm test -- --run && npm run typecheck && npm run lint`. Expected: all pass.
- [ ] **Step 5: Checkpoint.**

### Task 4: Jellyfin parsing, simulator and connector

**Files:**
- Create: `backend/src/linersync/adapters/connectors/jellyfin/__init__.py`, `parse.py`, `connector.py`
- Create: `backend/tests/adapters/connectors/jellyfin/__init__.py`, `sim.py`, `test_parse.py`, `test_connector.py`
- Create: `backend/tests/fixtures/jellyfin/items_playlists.json`, `playlist_items.json`, `search_audio.json`, `users.json`. Shape these from the Jellyfin OpenAPI spec (api.jellyfin.org, version 12.1) and keep them minimal. Register them in `test_shapes.py` like the other services.

**Interfaces:**
- Produces, in `parse.py`:
  - `SERVICE = ServiceId("jellyfin")`
  - `parse_track(item: Mapping[str, Any], base_url: str) -> Track | None`. Returns `None` for anything whose `Type != "Audio"`. Fields: `track_id=item["Id"]`, `title=Name`, `artists=tuple(Artists) or (AlbumArtist,)`, `album=Album`, `duration_ms=RunTimeTicks // 10_000`, `isrc=None`, `url=f"{base_url}/web/#/details?id={Id}"`.
  - `parse_playlist_meta(item, base_url) -> PlaylistMeta`. Uses `track_count=ChildCount`, `description=Overview or ""`, `version_token=None`, `owned=True`, and `cover_url` from the primary image tag if present.
  - `artwork_url(item, base_url) -> str | None`. Uses the item's own `ImageTags.Primary`, else `AlbumId` + `AlbumPrimaryImageTag`: `f"{base_url}/Items/{id}/Images/Primary?tag={tag}&maxWidth=300"`.
- Produces, in `connector.py`, `JellyfinConnector(api: HttpClient, base_url: str, user_id: Callable[[], Awaitable[str]], artwork: ArtworkHasher | None)`:
  - `service = SERVICE`, `display_name = "Jellyfin"`
  - `capabilities = frozenset({Capability.READ_ORDER})`
  - `limits = ConnectorLimits(page_size=100, add_batch_size=1, search_cost=1)`
  - `user_id` is a callable so Task 5 can resolve and cache it lazily.
- Produces, in `sim.py`, `JellyfinSim(*, catalog: Sequence[Track], api_key: str = "k" * 32, users: Sequence[tuple[str, str]] = (("Sam", "u" * 32),))`. API: `transport`, `add_playlist(name, track_ids) -> str` (32-hex ids), `add_non_audio(playlist_id)`, `fail_next(method, path_regex, status, *, times=1)`, `truncate(playlist_id)`, `change_during_read(playlist_id)`, `revoke_key()`, `requests`. Mirror `tests/adapters/connectors/spotify/sim.py`.
- Endpoints the connector uses (the sim implements exactly these):
  - `GET /Items?userId=…&IncludeItemTypes=Playlist&Recursive=true&Fields=ChildCount,Overview` → `{Items, TotalRecordCount}`
  - `GET /Playlists/{id}/Items?userId=…&StartIndex=…&Limit=…&Fields=ProviderIds,MediaSources` → `{Items, TotalRecordCount, StartIndex}`. Unknown id gives 404.
  - `GET /Items?userId=…&IncludeItemTypes=Audio&Recursive=true&SearchTerm=…&Limit=10`
  - `GET /Users` (used in Task 5)
  - `GET /Items/{id}/Images/Primary` (artwork)

- [ ] **Step 1: Write the failing parse tests** in `test_parse.py`, against the fixtures: `parse_track` maps `RunTimeTicks=2_000_000_000` to `duration_ms == 200_000`; it returns `None` for `Type: "Video"`; an item with empty `Artists` falls back to `AlbumArtist`; `artwork_url` prefers the item tag over the album tag.
- [ ] **Step 2: Run** `uv run pytest tests/adapters/connectors/jellyfin/test_parse.py -v`. Expected: FAIL (import error).
- [ ] **Step 3: Implement** `parse.py`.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Write the failing connector tests** in `test_connector.py`, against `JellyfinSim`:
  - `test_read_paginates_in_order_with_repeats`: 250 entries with repeats come back in order with `is_complete`.
  - `test_unknown_playlist_is_not_found`
  - `test_read_error_raises_read_failed`: 500 on every retry attempt.
  - `test_total_changing_mid_read_fails`: `change_during_read` gives `ReadFailed`.
  - `test_truncated_read_is_incomplete`
  - `test_non_audio_entry_makes_snapshot_incomplete`: `expected_total` counts the skipped entry, so `is_complete is False`.
  - `test_read_with_revoked_key_is_auth_required`: `ReadFailed` with `auth_required`.
  - `test_search_returns_audio_candidates`: a `TrackQuery` by title and artist finds the catalog track.
  - `test_writes_raise_write_failed_not_applied`: `create_playlist` and `apply(AddTracks)` raise `WriteFailed` with `maybe_applied is False`.
  - `test_list_playlists_metas`
- [ ] **Step 6: Run.** Expected: FAIL.
- [ ] **Step 7: Implement `JellyfinConnector`.**
  - **Reads:** loop pages until `len(entries) == total`, rejecting the read if `TotalRecordCount` differs between pages. Parse each item; when `parse_track` returns `None`, skip it but keep `expected_total` as Jellyfin's total. Map `HttpFailure` through `read_failed`, and a 404 to `PlaylistNotFound`.
  - **Search:** `SearchTerm=query.title`; return the parsed audio items.
  - **Artwork:** when `artwork` is set, hash `artwork_url` per track (as Spotify does).
  - **Writes:** follow `application/backup_source.py`, with the message `"Jellyfin is read-only"`.
- [ ] **Step 8: Run** `uv run pytest tests/adapters/connectors/jellyfin -v`. Expected: PASS.
- [ ] **Step 9: Checkpoint.**

### Task 5: Jellyfin plugin, registration and setup

**Files:**
- Create: `backend/src/linersync/adapters/connectors/jellyfin/plugin.py`
- Create: `backend/tests/adapters/connectors/jellyfin/test_plugin.py`
- Modify: `backend/pyproject.toml` `[project.entry-points."linersync.connectors"]`: add `jellyfin = "linersync.adapters.connectors.jellyfin.plugin:PLUGIN"`, then run `uv sync`.
- Modify: `backend/tests/test_layers.py` `FORBIDDEN`. Add `"linersync.adapters.connectors.jellyfin": (spotify, apple_music, "linersync.application")`, and add jellyfin to the kit's and each other service's forbidden list.
- Modify: `backend/src/linersync/adapters/connectors/kit/recording.py` `DEFAULT_REDACT`: add `api_key`, plus the `Authorization` header form `MediaBrowser Token=` if it isn't already redacted.
- Modify: `backend/tests/integration/test_real_connectors.py:199-202`: the discovery assertion includes `ServiceId("jellyfin")`.

**Interfaces:**
- Consumes: `JellyfinConnector`, `SERVICE` and `parse_*` from Task 4.
- Produces: `JELLYFIN_PLUGIN = PLUGIN = JellyfinPlugin()`, with:
  - `category = "self_hosted"`
  - `capabilities` and `limits` from the connector
  - `quota_policy = QuotaPolicy()`
  - `login_expires_at(...) -> None`
- Produces the credential keys `server_url`, `api_key`, `username` (from the form) and `user_id` (cached).
- Produces: `async def resolve_user_id(api: HttpClient, credentials: CredentialStore) -> str`. It returns the cached `user_id`, or looks up `GET /Users`, matches `Name` case-insensitively against `username`, saves `user_id` and returns it. On no match it raises `ReadFailed(SERVICE, f'No Jellyfin user named "{username}"')`.
- Setup guide steps, in order:
  1. INFO `before`: "LinerSync reads your Jellyfin playlists with an API key. It never changes anything on your server." Links to the Jellyfin docs.
  2. INFO `api_key`: "In Jellyfin, open Dashboard → API Keys, add a key named LinerSync, and copy it."
  3. FORM `details`:
     - `server_url`: TEXT, pattern `https?://.+`, hint "Start with http:// or https://"
     - `api_key`: SECRET, pattern `[0-9a-f]{32}`, hint "An API key is 32 characters, 0–9 and a–f"
     - `username`: TEXT, label "Jellyfin username", help "Whose playlists to read"
  4. TEST

- [ ] **Step 1: Write the failing tests** in `test_plugin.py`:
  - `test_guide_is_valid`: `check_guide(PLUGIN.setup_guide()) == []`.
  - `test_validate_normalises_server_url`: `"https://h/jellyfin/"` becomes `"https://h/jellyfin"`, and `"h.local"` is rejected.
  - `test_connection_ok`: returns `ConnectionTest(True, "Connected", account_name="Sam")` and stores `user_id`.
  - `test_connection_matches_username_case_insensitively`
  - `test_connection_unknown_user`: `ok False`, message `No Jellyfin user named "Kim"`.
  - `test_connection_bad_key`: `ok False`, message `Check your Jellyfin API key`.
  - `test_connection_unreachable`: `ok False` and does not raise.
  - `test_create_resolves_user_lazily`: a connector made before `user_id` is cached still lists playlists.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement** `plugin.py`. Build the API client from `credentials["server_url"]`, with auth headers `MediaBrowser Token="{api_key}"`. The `refresh()` method returns `False`, because a revoked key needs the user to act. Hash artwork with the same authenticated client: the images live on the user's own server.
- [ ] **Step 4: Run** `uv sync && uv run pytest tests/adapters/connectors/jellyfin tests/test_layers.py tests/integration/test_real_connectors.py -v`. Expected: PASS.
- [ ] **Step 5: Checkpoint.**

### Task 6: Contract harness and end-to-end run

**Files:**
- Modify: `backend/tests/contracts/harnesses.py`. Add `JellyfinHarness` (`missing_playlist_id = "0" * 32`, `make_playlist` uses `sim.add_playlist`, `inject` maps the four faults; the write faults are skipped by the laws anyway), and a `"jellyfin"` entry in `HARNESSES`. The catalog has 3 tracks with distinct titles and no ISRC.
- Modify: `backend/tests/integration/test_real_connectors.py`. Add `test_jellyfin_to_spotify_sync` using the existing `Rig` pattern (`:77-157`), with a Jellyfin sim as source and the Spotify sim as destination.

- [ ] **Step 1: Write the harness and the integration test.** The integration test asserts that a sync of a 3-track Jellyfin playlist creates a Spotify playlist with the 3 matched tracks. The Spotify sim catalog has matching titles, artists and durations, but the Jellyfin side has no ISRCs, so this exercises fuzzy matching.
- [ ] **Step 2: Run** `uv run pytest tests/contracts tests/integration -v`. Expected: every read law and `search_by_metadata_finds_track` PASS for jellyfin; `search_by_isrc_finds_track` and the write laws SKIP; the integration test PASSES.
- [ ] **Step 3: Fix any law failures in the connector, never in the laws.**
- [ ] **Step 4: Run the full backend gate:** `uv run pytest && uv run mypy && uv run ruff check`. Expected: all pass.
- [ ] **Step 5: Checkpoint.**

### Task 7: Live tests and docs

**Files:**
- Modify: `backend/tests/live/conftest.py` `LIVE_ENV`: add `"jellyfin": ("JELLYFIN_URL", "JELLYFIN_API_KEY", "JELLYFIN_USER")`.
- Modify: `backend/tests/live/test_live_contract.py`. Replace the `if service == "spotify" … else` block in `LiveHarness.__init__` with a per-service table `_LIVE_SETUP: dict[str, Callable[[Mapping[str, str]], tuple[ConnectorPlugin, dict[str, str]]]]`. Add `"jellyfin": "0" * 32` to `_MISSING_PLAYLIST_ID`, and add `"jellyfin"` to the fixture `params`. Jellyfin-specific harness behaviour:
  - **`load_catalog`:** take the first 3 items from `GET /Items?IncludeItemTypes=Audio&Recursive=true&Limit=3`, not `_SONGS` with ISRCs.
  - **`make_playlist`:** create the playlist out of band with `POST /Playlists` (`{"Name", "Ids", "UserId"}`) on a plain httpx client, since the connector can't create playlists.
- Modify: `docs/connectors.md`. §2: add jellyfin to the layout and entry-point block. §9: add the live env vars and a Jellyfin checklist (key revoked → attention shown; trailing-slash URL; playlist with a missing file shows as incomplete).
- Modify: `docs/server.md`: one paragraph under "Access setups" on reaching a Jellyfin server from the LinerSync container (same Docker network or LAN URL; Jellyfin needs no public URL).
- Modify: `README.md`: features and prerequisites list Jellyfin as read-only.

- [ ] **Step 1: Make the edits.**
- [ ] **Step 2: Run** `uv run pytest tests/live tests/deploy -v`. Expected: the live tests SKIP without `LINERSYNC_LIVE=1`, and the doc tests PASS (relative links resolve, the required README strings are still present).
- [ ] **Step 3: Ask the user** whether they want to run the live suite against their own Jellyfin (`LINERSYNC_LIVE=1 JELLYFIN_URL=… JELLYFIN_API_KEY=… JELLYFIN_USER=… uv run pytest -m live -k jellyfin`). Report the result as it comes back.
- [ ] **Step 4: Checkpoint.**
