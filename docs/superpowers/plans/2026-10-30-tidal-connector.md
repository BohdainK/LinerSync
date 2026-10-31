# Tidal Connector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read + write Tidal connector on the official v2 API, signed in with OAuth authorization code + PKCE, on par with Spotify.

**Architecture:** A new `adapters/connectors/tidal/` package (`parse.py`, `moves.py`, `auth.py`, `connector.py`, `plugin.py`) talks JSON:API to `https://openapi.tidal.com/v2` through the shared `kit/http.py` `HttpClient`. The PKCE token handling Spotify already has moves into `kit/oauth.py` so both services share it. Tidal removes and moves playlist entries by per-occurrence `itemId`, so position-exact removals and reorders need no Spotify-style delete-and-reinsert.

**Tech Stack:** Python 3.13, httpx, FastAPI, pytest (asyncio auto). No frontend changes: the UI is driven by the plugin's setup guide and capabilities.

**Spec:** `docs/superpowers/specs/2026-10-28-new-connectors-design.md` (section "Tidal", plus the per-connector checklist). This is plan 2 of 4; it builds on plan 1 (`2026-10-30-jellyfin-connector.md`), which already added destination gating, ISRC-less contract support and the per-service live table.

## API facts (from the TIDAL API OpenAPI spec 1.10.218, `tidal-music.github.io/tidal-api-reference/tidal-api-oas.json`)

- Authorize at `https://login.tidal.com/authorize`; tokens at `https://auth.tidal.com/v1/oauth2/token`. Public client: `client_id` in the form, no secret.
- Every request: `Authorization: Bearer …`, `Accept: application/vnd.api+json`; bodies are sent as `Content-Type: application/vnd.api+json`.
- `GET /users/me` → `data.attributes.{username, country}`.
- `GET /playlists?filter[owners.id]=me&countryCode=XX&include=coverArt` lists owned playlists; pages via `links.next` (a path relative to `/v2`, carrying `page[cursor]`). No offset, no total.
- `GET /playlists/{id}?countryCode=XX&include=coverArt` → `attributes.{name, description, numberOfItems, lastModifiedAt, accessType, playlistType}`. No revision is exposed for playlists.
- `GET /playlists/{id}/relationships/items?countryCode=XX&include=items.artists,items.albums.coverArt` → `data[]` of `{id, type: "tracks"|"videos", meta: {itemId, addedAt}}`, resources in `included`, next page in `links.next`.
- `POST /playlists/{id}/relationships/items` `{data: [{id, type}], meta: {onDuplicates: "ADD"}}` — at most 50 per request; the response's `meta.skipped` lists ids not added.
- `DELETE /playlists/{id}/relationships/items` `{data: [{id, type, meta: {itemId}}]}` — at most 50.
- `PATCH /playlists/{id}/relationships/items` `{data: [{id, type, meta: {itemId}}], meta: {positionBefore: <itemId>}}` moves the selected occurrences, in order, before that item; with `meta` omitted they go to the end. At most 20.
- `POST /playlists` `{data: {type: "playlists", attributes: {name, description, accessType}}}` (name ≤ 250, description ≤ 500, accessType `PUBLIC|UNLISTED`); `PATCH /playlists/{id}` with `{data: {type, id, attributes: {name, description}}}`.
- `GET /tracks?filter[isrc]=…&countryCode=XX&include=artists,albums.coverArt`; `GET /searchResults/{query}/relationships/tracks?countryCode=XX&include=tracks.artists,tracks.albums.coverArt`.
- Track `attributes.{title, version, isrc, duration}`; `duration` is ISO 8601 (`PT3M5S`). Artworks: `attributes.files[] = {href, meta: {width, height}}`.
- Every mutation accepts an `Idempotency-Key` header; a retry with the same key within an hour replays the original response.

## Global Constraints

- **No git commits.** The user commits manually. Each task ends with a "Checkpoint" step: list the changed files and stop for the user.
- No third-party Tidal SDK. All HTTP goes through `kit/http.py` `HttpClient`, built with `ctx.transport` and `ctx.sleep`. Artwork goes through a separate unauthenticated client, hashed with `ArtworkHasher`.
- A failed read raises `ReadFailed` and never returns an empty or partial snapshot as complete. Raise `PlaylistNotFound` only on an authoritative 404.
- Scopes, exactly: `playlists.read playlists.write search.read user.read collection.read collection.write`.
- Capabilities: `frozenset(Capability) - {Capability.DELETE_PLAYLIST}` (same as Spotify). `ConnectorLimits(page_size=50, add_batch_size=50, search_cost=2)`.
- Track ids are Tidal's track id as-is (`"75413016"`). Videos in a playlist get `"video:<id>"`; the prefix tells writes which JSON:API `type` to send.
- `version_token` is the playlist's `lastModifiedAt`. A read compares it before and after.
- Every POST, PATCH and DELETE sends a fresh `Idempotency-Key` (`uuid4().hex`) per logical request and is issued with `idempotent=True`, so the HTTP layer may safely retry it.
- New playlists are created `accessType: "UNLISTED"` (Tidal's closest to Spotify's private).
- Commands, run from `backend/`: `uv run pytest`, `uv run mypy`, `uv run ruff check`.

## Review Focus

1. **A playlist holding a track that is no longer available in the user's country** (the item is listed but has no resource in `included`). Expect: the snapshot is incomplete, not shortened or failed, and removals/reorders on it raise `WriteFailed(maybe_applied=False)`. Test: Task 3 `test_unavailable_track_makes_snapshot_incomplete`, Task 4 `test_remove_refuses_incomplete_playlist`.
2. **The same track several times in a playlist, removing only one copy.** Expect: exactly the copy at the requested position goes. Test: Task 4 `test_remove_one_of_duplicates_by_position`.
3. **A retried write after a timeout.** Expect: the retry carries the same `Idempotency-Key` and the sim applies it once. Test: Task 4 `test_add_retry_reuses_idempotency_key`.
4. **Playlist name over 250 or description over 500 characters** (copied from a service that allows more). Expect: cut to the limit, never a 400. Test: Task 4 `test_create_truncates_long_name_and_description`.
5. **Refresh token revoked at Tidal** (`400 invalid_grant`). Expect: the connection test says `Sign in to Tidal again`; reads raise `ReadFailed(auth_required=True)`; the dead token is discarded. Test: Task 5 `test_connection_after_revoked_refresh`.

---

### Task 1: Shared PKCE OAuth in the kit

**Files:**
- Create: `backend/src/linersync/adapters/connectors/kit/oauth.py`
- Modify: `backend/src/linersync/adapters/connectors/spotify/auth.py` (keep `SCOPES`, URLs, `LOGIN_LIFETIME`, `check_redirect_uri`, `authorize_url`; re-export `pkce_pair`)
- Test: `backend/tests/adapters/connectors/kit/test_oauth.py` (create); `tests/adapters/connectors/spotify/test_auth.py` must pass unchanged

**Interfaces:**
- Produces, in `kit/oauth.py`:
  - `pkce_pair(verifier: str | None = None) -> tuple[str, str]` (moved verbatim from Spotify).
  - `class PkceAuth` (an `AuthProvider`): `__init__(self, service: ServiceId, display_name: str, token_url: str, credentials: CredentialStore, token_client: HttpClient, clock: Clock)`, with `headers()`, `refresh()`, `exchange_code(code, redirect_uri, verifier)`. The body is today's `SpotifyAuth`, with `SERVICE`, `TOKEN_URL` and the word "Spotify" in every message replaced by the constructor values, e.g. `f"{display_name} sign-in expired; sign in again"`.
- `spotify/auth.py`: `class SpotifyAuth(PkceAuth)` with the old signature `(credentials, token_client, clock)`.

- [ ] **Step 1: Write the failing test** `test_messages_name_the_service`: a `PkceAuth(ServiceId("tidal"), "Tidal", …)` whose credentials hold no tokens raises `HttpFailure` from `headers()` with message `"Tidal sign-in expired; sign in again"` and `auth_required is True`.
- [ ] **Step 2: Run** `uv run pytest tests/adapters/connectors/kit/test_oauth.py -v`. Expected: FAIL (import error).
- [ ] **Step 3: Implement** `kit/oauth.py` and slim `spotify/auth.py` to the subclass.
- [ ] **Step 4: Run** `uv run pytest tests/adapters/connectors -v && uv run mypy`. Expected: PASS, Spotify tests untouched.
- [ ] **Step 5: Checkpoint.**

### Task 2: Tidal parsing and fixtures

**Files:**
- Create: `backend/src/linersync/adapters/connectors/tidal/__init__.py`, `parse.py`
- Create: `backend/tests/adapters/connectors/tidal/__init__.py`, `test_parse.py`
- Create: `backend/tests/fixtures/tidal/users_me.json`, `playlists.json`, `playlist.json`, `playlist_items.json` (one track, one video, one unavailable item), `tracks_isrc.json`, `search_tracks.json`, `create_playlist.json`. Shape them from the OpenAPI spec and keep them minimal.

**Interfaces:**
- Produces, in `parse.py`:
  - `SERVICE = ServiceId("tidal")`
  - `VIDEO_PREFIX = "video:"`; `track_ref(track_id: str) -> tuple[str, str]` returns `(type, id)`: `("videos", id)` for prefixed ids, else `("tracks", track_id)`.
  - `iso_duration_ms(value: str | None) -> int | None`: `"PT3M5S"` → `185_000`, `"PT1H2M3.5S"` → `3_723_500`, garbage or `None` → `None`.
  - `class Included`: built from a document's `included` list; `get(type: str, id: str) -> Mapping | None`.
  - `parse_track(resource: Mapping, included: Included, artwork_hash: str | None = None) -> Track`. Title is `title`, plus `f" ({version})"` when `version` is set. `artists` from the `artists` relationship in order, `album` from the first album's `title`, `isrc`, `duration_ms`, `url=f"https://tidal.com/browse/{'video' if video else 'track'}/{id}"`. Videos get the prefixed id and `isrc=None`.
  - `artwork_url(resource, included) -> str | None`: smallest file of the first album's first `coverArt` artwork.
  - `parse_playlist_meta(resource: Mapping, included: Included) -> PlaylistMeta`: `version_token=lastModifiedAt`, `track_count=numberOfItems`, `description or ""`, `owned=True`, `readable=True`, `cover_url` = largest file of the playlist's `coverArt`.
  - `@dataclass RawItem(ref_type: str, ref_id: str, item_id: str, added_at: datetime | None, resource: Mapping | None)` and `parse_items_page(doc) -> tuple[list[RawItem], str | None]` (items, next link).

- [ ] **Step 1: Write the failing tests** against the fixtures: the duration cases above; a track with `version: "Remastered"` gets title `"Song (Remastered)"`; artists keep relationship order; a video entry parses to `track_id == "video:<id>"`; the unavailable item has `resource is None`; `artwork_url` picks the smallest file; `parse_playlist_meta` takes `lastModifiedAt` as `version_token`; `track_ref("video:9") == ("videos", "9")` and `track_ref("9") == ("tracks", "9")`.
- [ ] **Step 2: Run** `uv run pytest tests/adapters/connectors/tidal/test_parse.py -v`. Expected: FAIL (import error).
- [ ] **Step 3: Implement** `parse.py`.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Checkpoint.**

### Task 3: Simulator, reads and search

**Files:**
- Create: `backend/src/linersync/adapters/connectors/tidal/connector.py` (reads and search; writes in Task 4)
- Create: `backend/tests/adapters/connectors/tidal/sim.py`, `test_connector.py`

**Interfaces:**
- Produces `TidalConnector(api: HttpClient, artwork: ArtworkHasher | None, country: Callable[[], Awaitable[str]], *, clock: Clock)`, `service = SERVICE`, `display_name = "Tidal"`, `CAPABILITIES`, `LIMITS` per Global Constraints. `API_BASE = "https://openapi.tidal.com/v2"` is defined in `parse.py`; `auth.py` (Task 5) re-exports it.
- Produces `TidalSim(*, catalog: Sequence[Track], access_token: str = "tidal-access", country: str = "NL", username: str = "sam")` on `httpx.MockTransport`, serving `openapi.tidal.com`, `auth.tidal.com` (token) and `resources.tidal.com` (artwork). API mirrors `SpotifySim`: `transport`, `add_playlist(name, track_ids) -> str` (UUID ids), `track_ids(pid)`, `item_ids(pid)`, `add_video(pid)`, `make_unavailable(pid, position)`, `fail_next(method, path_regex, status, *, times=1, apply_first=False)`, `timeout_next(method, path_regex, *, apply_first)`, `truncate(pid)`, `change_during_read(pid)`, `expire_login()`, `requests`. Pages of 20 items with `links.next`. Mutations remember `Idempotency-Key`s and replay the recorded response for a repeated key. `lastModifiedAt` advances on every change.

- [ ] **Step 1: Write the failing tests:**
  - `test_read_follows_pages_in_order_with_repeats`: 75 entries with repeats come back in order, `is_complete`, `version_token` set.
  - `test_list_playlists_follows_pages` (45 playlists).
  - `test_unknown_playlist_is_not_found`
  - `test_read_error_raises_read_failed` (500 on every attempt).
  - `test_change_during_read_fails` (`lastModifiedAt` differs after the items).
  - `test_truncated_read_is_incomplete` (`numberOfItems` exceeds the items served → `is_complete is False`).
  - `test_unavailable_track_makes_snapshot_incomplete`
  - `test_video_entry_reads_with_prefixed_id`
  - `test_read_sends_country_code`: every API GET carries `countryCode=NL`.
  - `test_search_by_isrc_first`: an ISRC query hits `/tracks?filter[isrc]=…` and never `/searchResults`.
  - `test_search_falls_back_to_metadata`: no ISRC hit → `/searchResults/{title artist}/relationships/tracks`, the query URL-encoded in the path.
  - `test_expired_token_is_auth_required`: `expire_login()` + a dead refresh → `ReadFailed(auth_required=True)`.
- [ ] **Step 2: Run** `uv run pytest tests/adapters/connectors/tidal/test_connector.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement the reads.** `read_playlist`: GET the playlist (404 → `PlaylistNotFound`), follow item pages until `links.next` is absent, GET the playlist again and raise `ReadFailed` if `lastModifiedAt` moved. `expected_total = numberOfItems`; items with no resource are left out of `entries` (so the snapshot is incomplete). More items than `numberOfItems` → `ReadFailed`. Parse failures (`KeyError`, `TypeError`, `ValueError`) → `ReadFailed("Tidal's reply for … was not understood")`, as Spotify does.
- [ ] **Step 4: Implement search.** ISRC first (`filter[isrc]`), then metadata with the query `f"{title} {artists[0]}"`; return at most 10 parsed tracks.
- [ ] **Step 5: Run.** Expected: PASS.
- [ ] **Step 6: Checkpoint.**

### Task 4: Writes

**Files:**
- Create: `backend/src/linersync/adapters/connectors/tidal/moves.py`
- Create: `backend/tests/adapters/connectors/tidal/test_moves.py`
- Modify: `backend/src/linersync/adapters/connectors/tidal/connector.py`, `tests/adapters/connectors/tidal/test_connector.py`

**Interfaces:**
- Produces, in `moves.py`:
  - `@dataclass Move(item_ids: tuple[str, ...], before: str | None)` — `before=None` means "to the end".
  - `plan_moves(current: Sequence[tuple[str, str]], target: Sequence[str]) -> list[Move]`, where `current` is `(track_id, item_id)` per position. Raises `ValueError` if `target` isn't a permutation of the current track ids. Algorithm: label occurrences (`n`-th copy of a track id in `current` is the `n`-th in `target`); keep a longest increasing subsequence of current positions in target order; walk `target` from the end, and for each maximal run of non-kept labels emit one move of their item ids, in target order, before the item id that follows the run in `target` (or `None` at the end). Runs longer than 20 are split into consecutive moves with the same anchor, last chunk first.
  - `apply_moves(current: Sequence[tuple[str, str]], moves: Sequence[Move]) -> list[tuple[str, str]]` for tests and the sim.
- Connector writes:
  - `create_playlist`: POST `/playlists` with name cut to 250 and description to 500, `accessType: "UNLISTED"`. `draft.cover_url` is ignored (the API has no cover upload).
  - `AddTracks`: POST in batches of 50 via `apply_in_batches`, `onDuplicates: "ADD"`.
  - `RemoveTracks`: re-read the raw items, map each `Removal.position` to that position's `itemId` (the track id there must match, else `WriteFailed("The Tidal playlist changed since it was planned; it will be replanned", False)`), DELETE in batches of 50.
  - `Reorder`: re-read, `plan_moves`, PATCH each move.
  - `UpdateMetadata`: PATCH `/playlists/{id}` with name and description, same cuts.
  - Removes and reorders on a playlist whose re-read is incomplete raise `WriteFailed("Tidal can't change this playlist while it holds songs that aren't available", False)`.
  - Failures map through `write_failed`; once any request of a multi-request op has been sent, later failures are `maybe_applied=True`.

- [ ] **Step 1: Write the failing `test_moves.py`:** a property-style test over 200 seeded random permutations (with duplicates) that `apply_moves(current, plan_moves(current, target))` yields `target`'s track ids; `test_already_ordered_needs_no_moves`; `test_long_run_splits_into_twenty`; `test_not_a_permutation_raises`.
- [ ] **Step 2: Write the failing connector tests:** `test_create_playlist_is_unlisted`, `test_create_truncates_long_name_and_description`, `test_add_batches_of_fifty` (120 ids → 3 POSTs), `test_add_video_sends_videos_type`, `test_add_retry_reuses_idempotency_key` (`timeout_next("POST", …, apply_first=True)`, then the tracks appear once), `test_remove_one_of_duplicates_by_position`, `test_remove_refuses_incomplete_playlist`, `test_remove_changed_playlist_is_replanned`, `test_reorder_reaches_target` (30 tracks, reversed), `test_update_metadata_patches_name_and_description`, `test_write_rejected_is_not_applied` (403).
- [ ] **Step 3: Run** `uv run pytest tests/adapters/connectors/tidal -v`. Expected: FAIL.
- [ ] **Step 4: Implement** `moves.py` and the writes.
- [ ] **Step 5: Run.** Expected: PASS.
- [ ] **Step 6: Checkpoint.**

### Task 5: Auth, plugin and registration

**Files:**
- Create: `backend/src/linersync/adapters/connectors/tidal/auth.py`, `plugin.py`
- Create: `backend/tests/adapters/connectors/tidal/test_auth.py`, `test_plugin.py`
- Modify: `backend/pyproject.toml` entry points: `tidal = "linersync.adapters.connectors.tidal.plugin:PLUGIN"`, then `uv sync`.
- Modify: `backend/tests/test_layers.py` `FORBIDDEN`: a `tidal` entry forbidding the other three services and `linersync.application`; add tidal to the kit's and every other service's list.
- Modify: `backend/tests/integration/test_real_connectors.py` discovery assertion: include `ServiceId("tidal")`.
- `RecordingTransport.DEFAULT_REDACT` already covers `authorization`, `access_token`, `refresh_token`, `code`, `code_verifier`, `email`; add `firstName`, `lastName` (Tidal's `/users/me`).

**Interfaces:**
- `auth.py`: `SERVICE`, `SCOPES`, `AUTHORIZE_URL = "https://login.tidal.com/authorize"`, `TOKEN_URL = "https://auth.tidal.com/v1/oauth2/token"`, `API_BASE`; `class TidalAuth(PkceAuth)` with `(credentials, token_client, clock)`; `authorize_url(client_id, redirect_uri, state, challenge) -> str`; `check_redirect_uri(uri)` accepting `https://…` and `http://127.0.0.1` / `http://localhost`, else `ValueError("Tidal needs an https:// address, or http://127.0.0.1 for local use")`.
- `plugin.py`: `TIDAL_PLUGIN = PLUGIN = TidalPlugin()`, implementing `ConnectorPlugin` and `OAuthSetup`. `category = "streaming"`, `quota_policy = QuotaPolicy()`. `complete()` exchanges the code, then GETs `/users/me` and saves `user_id`, `username` and `country_code`. `resolve_country(api, credentials) -> str` returns the cached `country_code` or fetches and saves it. `test_connection` returns `ConnectionTest(True, "Connected", username)`; on `auth_required`, `ConnectionTest(False, "Sign in to Tidal again")`. `login_expires_at` returns `None` (Tidal documents no login lifetime).
- The API client sends `Accept: application/vnd.api+json` on every request (pass it per request from the connector's `_get` and mutation helpers, as `headers=`).
- Setup guide, in order:
  1. INFO `before`: "LinerSync signs in to Tidal with an app that you create yourself. It's free and takes a few minutes."
  2. INFO `create_app`: "Open the Tidal developer dashboard and create an app. Add the Redirect URI below to the app, and give it the scopes playlists.read, playlists.write, search.read, user.read, collection.read and collection.write." Link `https://developer.tidal.com/dashboard`; copy value `Redirect URI` = `{redirect_uri}`.
  3. FORM `client_id`: field `client_id`, label "Client ID", pattern `[A-Za-z0-9]{8,64}`, hint "Copy the Client ID exactly as the dashboard shows it".
  4. OAUTH `sign_in`: "Sign in and allow LinerSync to manage your playlists."
  5. TEST

- [ ] **Step 1: Write the failing tests.** `test_auth.py`: `authorize_url` carries the six scopes space-separated, `code_challenge_method=S256` and the state; `check_redirect_uri` cases. `test_plugin.py`: `test_guide_is_valid` (`check_guide(...) == []`); `test_complete_saves_user_and_country`; `test_connection_ok` (account name `sam`); `test_connection_after_revoked_refresh` (token endpoint answers `400 invalid_grant`: message `Sign in to Tidal again`, `refresh_token` cleared); `test_create_resolves_country_lazily` (a connector built before `country_code` is cached still lists playlists, and caches it).
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement** `auth.py` and `plugin.py`, and make the registration edits.
- [ ] **Step 4: Run** `uv sync && uv run pytest tests/adapters/connectors/tidal tests/test_layers.py tests/integration/test_real_connectors.py -v`. Expected: PASS.
- [ ] **Step 5: Checkpoint.**

### Task 6: Contract harness and end-to-end run

**Files:**
- Modify: `backend/tests/contracts/harnesses.py`: `TidalHarness` (catalog of 3 tracks with distinct titles and ISRCs, `missing_playlist_id = "00000000-0000-0000-0000-000000000000"`, `make_playlist` via `sim.add_playlist`, `inject` maps the four faults on `/playlists/{id}/relationships/items` like `SpotifyHarness`), plus a `"tidal"` entry in `HARNESSES`.
- Modify: `backend/tests/integration/test_real_connectors.py`: `test_spotify_to_tidal_sync` with the existing `Rig` pattern; the Tidal catalog shares ISRCs with the Spotify one.

- [ ] **Step 1: Write the harness and the integration test.** The integration test syncs a 3-track Spotify playlist to a new Tidal playlist, then removes the middle track on Spotify and syncs again: the Tidal playlist ends with the other two, in order.
- [ ] **Step 2: Run** `uv run pytest tests/contracts tests/integration -v`. Expected: every law PASSES for tidal (none skip except those the harness can't arrange).
- [ ] **Step 3: Fix any law failures in the connector, never in the laws.**
- [ ] **Step 4: Run the full backend gate:** `uv run pytest && uv run mypy && uv run ruff check`. Expected: all pass.
- [ ] **Step 5: Checkpoint.**

### Task 7: Live login, live tests and docs

**Files:**
- Modify: `backend/src/linersync/devtools/live_login.py`: a `tidal` subcommand (`--client-id`, `--port 8767`) reusing the Spotify loopback flow; generalise `spotify_callback` into `oauth_callback(query, state, display_name)` and keep `spotify_callback` as a thin wrapper so existing tests pass. Prints `TIDAL_CLIENT_ID`, `TIDAL_REFRESH_TOKEN`, `LINERSYNC_LIVE=1`.
- Modify: `backend/tests/devtools/test_live_login.py`: `tidal_login_url` points at `login.tidal.com` with the redirect `http://127.0.0.1:8767/callback`; `oauth_callback` reports `Tidal sign-in failed: access_denied`.
- Modify: `backend/tests/live/conftest.py` `LIVE_ENV`: `"tidal": ("TIDAL_CLIENT_ID", "TIDAL_REFRESH_TOKEN")`.
- Modify: `backend/tests/live/test_live_contract.py`: `_tidal_setup`, `_MISSING_PLAYLIST_ID["tidal"]`, and `"tidal"` in the fixture params.
- Modify: `docs/connectors.md` §2 (layout and entry points), §9 (env vars, the `live_login tidal` command, and a Tidal checklist: a revoked app → attention shown; a playlist with an unavailable track reads as incomplete; removing one of two copies removes the right one; that `lastModifiedAt` changes when items are added, removed and moved — if it doesn't, the read check is blind and must change).
- Modify: `docs/server.md`: the Tidal redirect URI note next to Spotify's.
- Modify: `README.md`: features and prerequisites list Tidal (a free Tidal developer app; a Tidal subscription).

- [ ] **Step 1: Write the failing live-login tests, then implement the subcommand.**
- [ ] **Step 2: Make the live wiring and doc edits.**
- [ ] **Step 3: Run** `uv run pytest tests/devtools tests/live tests/deploy -v`. Expected: live tests SKIP without `LINERSYNC_LIVE=1`; the rest PASS.
- [ ] **Step 4: Ask the user** whether to run the live suite against their own Tidal (`python -m linersync.devtools.live_login tidal --client-id …`, then `uv run pytest -m live -k tidal`). The capabilities and the `lastModifiedAt` assumption are only confirmed by that run; report the result as it comes back.
- [ ] **Step 5: Checkpoint.**
