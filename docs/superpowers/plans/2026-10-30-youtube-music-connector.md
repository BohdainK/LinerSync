# YouTube Music Connector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read + write YouTube Music connector on the internal `youtubei/v1` API, signed in either with browser cookies or with Google's OAuth device-code flow, and teach matching to trust an ISRC search hit that agrees on title, artists and duration.

**Architecture:** A new `adapters/connectors/youtube_music/` package (`parse.py`, `moves.py`, `auth.py`, `connector.py`, `plugin.py`) talks to `https://music.youtube.com/youtubei/v1` through `kit/http.py` `HttpClient`. Response-shape knowledge is ported from ytmusicapi (MIT, credited) and isolated in `parse.py`, tested against responses recorded from the real service. A generic `DEVICE_CODE` setup step (backend protocol, server routes, frontend step) carries the OAuth flow. A new optional `Track.found_by_isrc` flag lets `domain/matching` floor confidence for an agreeing ISRC search hit without knowing any service.

**Tech Stack:** Python 3.13, httpx, FastAPI, pytest; React + TypeScript + vitest + TanStack Query.

**Spec:** `docs/superpowers/specs/2026-10-28-new-connectors-design.md` (section "YouTube Music", cross-cutting change 2, the per-connector checklist). Plan 3 of 4; builds on plans 1 (Jellyfin) and 2 (Tidal).

## API facts

Recorded from the real service on 2026-10-30 (unauthenticated, `WEB_REMIX` client), trimmed into `backend/tests/fixtures/youtube_music/recorded/`:

- `POST /youtubei/v1/search?prettyPrint=false` with `{context, query, params: "EgWKAQIIAWoMEA4QChADEAQQCRAF"}` (songs filter). Results: `contents.tabbedSearchResultsRenderer.tabs[0].tabRenderer.content.sectionListRenderer.contents[*].musicShelfRenderer.contents[*].musicResponsiveListItemRenderer`. Column 0's first run has the title and `navigationEndpoint.watchEndpoint.{videoId, watchEndpointMusicSupportedConfigs.watchEndpointMusicConfig.musicVideoType}`. Column 1's runs are `artist • album • m:ss`; artist and album runs carry `browseEndpoint...pageType` `MUSIC_PAGE_TYPE_ARTIST` / `MUSIC_PAGE_TYPE_ALBUM`. The query `GBAYE0601498` (an ISRC) returned "Yellow Submarine (Remastered 2009)" as an Art Track (`MUSIC_VIDEO_TYPE_ATV`) first.
- `POST /youtubei/v1/browse` with `{browseId: "VL<playlistId>"}`: header at `contents.twoColumnBrowseResultsRenderer.tabs[0].tabRenderer.content.sectionListRenderer.contents[0].musicResponsiveHeaderRenderer` (`title.runs`, `description`, `secondSubtitle.runs[0].text` = `"218 songs"`); items at `contents.twoColumnBrowseResultsRenderer.secondaryContents.sectionListRenderer.contents[0].musicPlaylistShelfRenderer.contents`, each with `playlistItemData.{videoId, playlistSetVideoId}`, `flexColumns` (title / artists / album) and `fixedColumns[0]` duration text. 100 items per page; the 101st entry is a `continuationItemRenderer` whose `continuationEndpoint.continuationCommand.token` is posted as `{continuation: token}` to `/browse`; the reply's items are in `onResponseReceivedActions[0].appendContinuationItemsAction.continuationItems`.

From ytmusicapi (authenticated, so not recorded; on the live checklist):

- Library playlists: `/browse` `{browseId: "FEmusic_liked_playlists"}` → `contents.singleColumnBrowseResultsRenderer.tabs[0].tabRenderer.content.sectionListRenderer.contents[0].gridRenderer.items[*].musicTwoRowItemRenderer` (title run's `browseEndpoint.browseId` = `VL…`; the "New playlist" tile has none). Further pages via `gridRenderer.continuations[0].nextContinuationData.continuation`, answered in `continuationContents.gridContinuation.items`.
- An owned playlist's header is `musicEditablePlaylistDetailHeaderRenderer.header.musicResponsiveHeaderRenderer` (same fields as above).
- `POST /playlist/create` `{title, description, privacyStatus: "PRIVATE"}` → `{playlistId}`.
- `POST /browse/edit_playlist` `{playlistId, actions: [...]}` with actions `ACTION_ADD_VIDEO {addedVideoId}`, `ACTION_REMOVE_VIDEO {removedVideoId, setVideoId}`, `ACTION_MOVE_VIDEO_BEFORE {setVideoId, movedSetVideoIdSuccessor?}` (no successor = to the end), `ACTION_SET_PLAYLIST_NAME {playlistName}`, `ACTION_SET_PLAYLIST_DESCRIPTION {playlistDescription}` → `{status: "STATUS_SUCCEEDED", playlistEditResults: [...]}`.
- `POST /account/account_menu` → `actions[0].openPopupAction.popup.multiPageMenuRenderer.header.activeAccountHeaderRenderer.accountName.runs[0].text`.
- Cookie auth: headers `Cookie`, `Authorization: SAPISIDHASH <ts>_<sha1("<ts> <SAPISID> https://music.youtube.com")>`, `X-Origin: https://music.youtube.com`, `X-Goog-AuthUser: 0`. OAuth: `Authorization: Bearer …`. Google device flow: `POST https://oauth2.googleapis.com/device/code {client_id, scope: "https://www.googleapis.com/auth/youtube"}` → `{device_code, user_code, verification_url, expires_in, interval}`; poll `POST https://oauth2.googleapis.com/token {client_id, client_secret, device_code, grant_type: "urn:ietf:params:oauth:grant-type:device_code"}` → `authorization_pending` / `slow_down` / `access_denied` / `expired_token` or tokens; refresh with `grant_type=refresh_token` plus `client_secret`.

## Global Constraints

- **No git commits.** Each task ends with a "Checkpoint" step.
- No ytmusicapi dependency. Credit it in `youtube_music/parse.py`'s docstring: "Response shapes ported from ytmusicapi (MIT), https://github.com/sigma67/ytmusicapi".
- All HTTP through `HttpClient`; artwork through an unauthenticated client.
- Every request body carries `context = {"client": {"clientName": "WEB_REMIX", "clientVersion": CLIENT_VERSION, "hl": "en"}, "user": {}}` with `CLIENT_VERSION = "1.20251020.03.00"`; URLs carry `prettyPrint=false`.
- `Track.track_id` is the `videoId`; `Track.isrc` is always `None`. Never copy a query's ISRC onto a candidate.
- Capabilities: `READ_ORDER, ADD, REMOVE, REORDER, EDIT_METADATA, CREATE_PLAYLIST`. `ConnectorLimits(page_size=100, add_batch_size=50, search_cost=1)`.
- Auth mode from env var `LINERSYNC_YTMUSIC_AUTH`: `headers` (default) or `oauth`, like `LINERSYNC_APPLE_MUSIC_AUTH`.
- Matching: an ISRC search hit is floored at `0.95` only when the source has an ISRC, the candidate has `found_by_isrc`, title similarity ≥ `0.9`, artist similarity ≥ `0.5`, duration within `cfg.duration_full_s`, and no version differs. Reason text: `"found by ISRC search"`.
- Writes are not idempotent on YouTube: `edit_playlist` and `create` go out with `idempotent=False`.
- Copy: header-mode login lifetime is 30 days from when the cookie was first seen by a passing test.
- Commands, from `backend/`: `uv run pytest`, `uv run mypy --strict src tests`, `uv run ruff check src tests`, `uv run ruff format --check src tests`. From `frontend/`: `npm run check`. After backend API changes: `uv run python -m linersync.server.openapi > ../frontend/openapi.json` then `npm run api:gen`.

## Review Focus

1. **A song greyed out in a playlist** (removed from YouTube, region-blocked): no `playlistItemData.videoId`. Expect: the snapshot is incomplete and removals/reorders refuse with `WriteFailed(maybe_applied=False)`. Test: Task 4 `test_greyed_out_item_makes_snapshot_incomplete`, Task 5 `test_remove_refuses_incomplete_playlist`.
2. **An ISRC query whose top hit is an unrelated song** (the spec saw this with a bogus ISRC). Expect: no floor; it is scored as plain metadata. Test: Task 1 `test_isrc_hit_that_disagrees_gets_no_floor`.
3. **A pasted cookie without `SAPISID`** (signed-out tab, wrong site). Expect: the form rejects it with `The cookie has no SAPISID; copy it from a signed-in music.youtube.com tab`. Test: Task 6 `test_validate_rejects_cookie_without_sapisid`.
4. **The user closes the device-code page or waits too long.** Expect: polling reports `expired` / `denied` with a message, and starting again issues a new code. Test: Task 2 `test_device_code_expired`, `test_device_code_denied`.
5. **Search results that are only music videos** (no Art Track). Expect: they're kept (never an empty result because ATVs are missing). Test: Task 4 `test_videos_kept_when_no_art_track`.

---

### Task 1: Matching trusts an agreeing ISRC search hit

**Files:**
- Modify: `backend/src/linersync/domain/tracks.py` (`Track`)
- Modify: `backend/src/linersync/domain/matching/scoring.py`
- Test: `backend/tests/domain/matching/test_scoring.py`

**Interfaces:**
- Produces: `Track.found_by_isrc: bool = field(default=False, compare=False)` — set by a connector on a candidate returned for an ISRC query on a service that doesn't expose ISRCs.
- Produces: in `score_candidate`, after the artwork adjustment and before the equal-ISRC floor, the floor and reason from Global Constraints. `_ISRC_SEARCH_FLOOR = 0.95`.

- [ ] **Step 1: Write the failing tests:** `test_agreeing_isrc_hit_is_floored` (source with ISRC; candidate same title/artist, 1 s apart, `found_by_isrc=True`, but a weak base score: give the source a featured-artist suffix so the base is under 0.95 → confidence `== 0.95`, reason present); `test_isrc_hit_that_disagrees_gets_no_floor` (different title → no floor, no reason); `test_isrc_hit_needs_source_isrc`; `test_isrc_hit_far_duration_gets_no_floor` (5 s apart); `test_found_by_isrc_ignored_in_equality` (`replace(t, found_by_isrc=True) == t`).
- [ ] **Step 2: Run** `uv run pytest tests/domain/matching -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest tests/domain tests/adapters/sqlite -v`. Expected: PASS (stored runs still decode).
- [ ] **Step 5: Checkpoint.**

### Task 2: Generic `DEVICE_CODE` setup step

**Files:**
- Modify: `backend/src/linersync/ports/setup.py` (`StepKind.DEVICE_CODE = "device_code"`)
- Modify: `backend/src/linersync/adapters/connectors/kit/plugin.py` (protocol + dataclasses)
- Modify: `backend/src/linersync/server/connections.py`, `backend/src/linersync/server/api/connections.py`, `backend/src/linersync/server/api/schemas.py`
- Modify: `frontend/openapi.json`, `frontend/src/api/schema.d.ts` (regenerated), `frontend/src/features/setup/stepStates.ts`, `SetupStepper.tsx`
- Create: `frontend/src/features/setup/steps/DeviceCodeStep.tsx`
- Test: `backend/tests/server/test_connections_api.py` (or the file that tests OAuth routes), `frontend/src/features/setup/SetupStepper.test.tsx`, `stepStates.test.ts`

**Interfaces:**
- `kit/plugin.py`:
  - `@dataclass DeviceCodeStart(user_code: str, verification_url: str, interval_s: int, expires_in_s: int, keep: dict[str, str])`
  - `DevicePollStatus = Literal["pending", "done", "denied", "expired"]`; `@dataclass DevicePoll(status: DevicePollStatus, message: str = "", interval_s: int | None = None)`
  - `@runtime_checkable class DeviceCodeSetup(Protocol)`: `async def start_device_code(self, ctx) -> DeviceCodeStart`; `async def poll_device_code(self, ctx, keep: Mapping[str, str]) -> DevicePoll` (saves tokens itself on `done`).
- `ConnectionManager`: `async start_device_code(service) -> DeviceCodeStart` (requires set-up values, replaces any pending flow for the service, remembers `keep` and an expiry); `async poll_device_code(service) -> DevicePoll` (no pending flow → `DevicePoll("expired", "Start again")`; on `done` saves `UNTESTED` and forgets the flow; on `denied`/`expired` forgets it).
- Routes: `POST /api/connections/{service}/device-code/start` → `DeviceCodeOut {user_code, verification_url, interval_s, expires_in_s}`; `POST /api/connections/{service}/device-code/poll` → `DevicePollOut {status, message}`. A plugin without the protocol → 409 `"<Name> doesn't use a device code"`.
- Frontend `DeviceCodeStep({service, name, step, onSignedIn})`: a "Get a code" button; then shows the code in large monospace text with a copy button and an "Open <verification_url>" link, polls every `interval_s` seconds; `done` → `onSignedIn()`; `denied`/`expired` → the message and the button again. `stepStates` treats `device_code` like `oauth`.

- [ ] **Step 1: Write the failing backend tests** with a test plugin implementing `DeviceCodeSetup` (add it to `tests/adapters/connectors/kit/plugins_for_tests.py`): start returns the code; poll pending → `pending`; poll after approval → `done` and status `untested`; `test_device_code_expired`; `test_device_code_denied`; poll without start → `expired`; 409 for a plugin without the protocol.
- [ ] **Step 2: Run** them. Expected: FAIL.
- [ ] **Step 3: Implement the backend; regenerate `openapi.json` and `schema.d.ts`.**
- [ ] **Step 4: Run** `uv run pytest tests/server -v`. Expected: PASS.
- [ ] **Step 5: Write the failing frontend tests:** a guide with a `device_code` step shows "Get a code"; after clicking, the code `ABCD-EFGH` and the link appear; a `done` poll marks the step done and runs the connection test; an `expired` poll shows its message.
- [ ] **Step 6: Implement** `DeviceCodeStep` and the wiring. **Run** `npm run check`. Expected: PASS.
- [ ] **Step 7: Checkpoint.**

### Task 3: Parsing

**Files:**
- Create: `backend/src/linersync/adapters/connectors/youtube_music/__init__.py`, `parse.py`
- Create: `backend/tests/adapters/connectors/youtube_music/__init__.py`, `test_parse.py`
- Fixtures: the recorded `search_songs.json`, `playlist.json`, `playlist_continuation.json` (exist), plus hand-shaped `library_playlists.json`, `owned_playlist.json` (editable header, one greyed-out item), `edit_playlist.json`, `create_playlist.json`, `account_menu.json`.

**Interfaces (all pure):**
- `SERVICE = ServiceId("youtube_music")`, `API_BASE = "https://music.youtube.com/youtubei/v1"`, `CLIENT_VERSION`, `context() -> dict`.
- `ATV = "MUSIC_VIDEO_TYPE_ATV"`.
- `parse_duration(text: str) -> int | None` (`"4:03"` → 243000, `"1:02:03"` → 3723000).
- `@dataclass Song(track: Track, set_video_id: str | None, video_type: str | None)`; `parse_item(renderer) -> Song | None` (`None` for a greyed-out item with no `videoId`).
- `@dataclass PlaylistPage(name: str, description: str, track_count: int | None, items: list[Song | None], continuation: str | None)`; `parse_playlist(doc) -> PlaylistPage` (handles both header shapes); `parse_continuation(doc) -> tuple[list[Song | None], str | None]`.
- `parse_library(doc) -> tuple[list[PlaylistMeta], str | None]` and its continuation form; `parse_search(doc) -> list[Song]`; `parse_account_name(doc) -> str | None`; `parse_edit_status(doc) -> bool`.
- `track_count` from text like `"218 songs"` / `"1 song"` / `"1,024 songs"`.

- [ ] **Step 1: Write the failing tests** against the fixtures: the recorded search's first song is `RhoprlMxteE`, title "Yellow Submarine (Remastered 2009)", artists `("The Beatles",)`, album "Revolver (Remastered 2009)", 159000 ms, `video_type == ATV`, `isrc is None`; the recorded playlist page has name "The Beatles Jukebox", `track_count == 218`, 3 songs with set ids, and a continuation token; the continuation parses 2 songs and no token; the greyed item is `None`; library parsing skips the "New playlist" tile and strips `VL`; durations and counts as above.
- [ ] **Step 2: Run.** Expected: FAIL. **Step 3: Implement.** **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Checkpoint.**

### Task 4: Simulator, reads and search

**Files:**
- Create: `backend/src/linersync/adapters/connectors/youtube_music/connector.py`
- Create: `backend/tests/adapters/connectors/youtube_music/sim.py`, `test_connector.py`

**Interfaces:**
- `YouTubeMusicConnector(api: HttpClient, artwork: ArtworkHasher | None, *, clock: Clock)`; all calls are `POST`s with `json={"context": context(), ...}`.
- `YouTubeMusicSim(*, catalog: Sequence[Track], isrcs: Mapping[str, str] = {}, video_types: Mapping[str, str] = {})` serving `music.youtube.com` (+ `oauth2.googleapis.com`, + image host). `isrcs` maps an ISRC to the videoId its search returns first. Same fault API as the Tidal sim (`fail_next`, `timeout_next`, `truncate`, `change_during_read`, `grey_out`, `expire_login`), pages of 100, both header shapes. It answers search for any query by word match, an ISRC query with the mapped video first, and unknown ISRCs with an unrelated song (as the real service does).
- Reads: page through continuations; `expected_total = track_count`; greyed-out items are left out. Change check: after the last page, fetch the first page again and compare its `setVideoId`s and the count; a difference → `ReadFailed("… changed while it was being read …")`. `version_token` is `None`.
- Search: with `query.isrc`, search the ISRC first and mark those results `found_by_isrc=True`; then (if no ISRC or nothing came back) search `"<artist> <title>"`. When any result is an ATV, drop non-ATV results. At most 10.

- [ ] **Step 1: Write the failing tests:** `test_read_follows_continuations_in_order_with_repeats` (250 entries), `test_list_playlists_follows_continuations`, `test_unknown_playlist_is_not_found` (the sim answers an unknown `VL…` with 404), `test_read_error_raises_read_failed`, `test_change_during_read_fails`, `test_truncated_read_is_incomplete`, `test_greyed_out_item_makes_snapshot_incomplete`, `test_isrc_search_marks_hits`, `test_isrc_never_copied_onto_candidate`, `test_metadata_search_when_no_isrc`, `test_art_tracks_preferred`, `test_videos_kept_when_no_art_track`, and a shape test comparing sim responses with the fixtures (`key_paths` subset), as for Tidal.
- [ ] **Step 2: Run.** Expected: FAIL. **Step 3: Implement.** **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Checkpoint.**

### Task 5: Writes

**Files:**
- Create: `backend/src/linersync/adapters/connectors/youtube_music/moves.py`, `tests/adapters/connectors/youtube_music/test_moves.py`
- Modify: `connector.py`, `test_connector.py`

**Interfaces:**
- `moves.plan_moves(current: Sequence[tuple[str, str]], target: Sequence[str]) -> list[tuple[str, str | None]]` — `(setVideoId to move, setVideoId to put it before or None = end)`, one action per moved occurrence, walking the target from the end like Tidal's planner (no chunking: actions go in one request). `apply_moves` for tests.
- `create_playlist`: `playlist/create` with name and description (YouTube caps: name 150, description 5000 — cut to those), `privacyStatus: "PRIVATE"`; returns a `PlaylistMeta` with `track_count=0`.
- `AddTracks`: `ACTION_ADD_VIDEO` actions, 50 per request, through `apply_in_batches`.
- `RemoveTracks`: re-read; check each position holds the track id; `ACTION_REMOVE_VIDEO` with that occurrence's `setVideoId`; 50 per request.
- `Reorder`: re-read; `plan_moves`; one `edit_playlist` with all `ACTION_MOVE_VIDEO_BEFORE` actions (split at 50, each later request `maybe_applied=True` on failure).
- `UpdateMetadata`: one request with `ACTION_SET_PLAYLIST_NAME` and `ACTION_SET_PLAYLIST_DESCRIPTION`.
- A 200 whose `status` isn't `STATUS_SUCCEEDED` → `WriteFailed("YouTube Music didn't make the change", maybe_applied=False)`.
- Removes and reorders on an incomplete re-read → `WriteFailed(..., False)`.

- [ ] **Step 1: Write the failing tests:** moves property test (200 seeded permutations with duplicates) and `test_already_ordered_needs_no_moves`; connector: `test_create_playlist_is_private`, `test_create_truncates_long_name`, `test_add_batches_of_fifty`, `test_add_keeps_duplicates` (no `dedupeOption`), `test_remove_one_of_duplicates_by_position`, `test_remove_refuses_incomplete_playlist`, `test_remove_changed_playlist_is_replanned`, `test_reorder_reaches_target`, `test_update_metadata`, `test_status_failed_is_not_applied`, `test_write_timeout_is_maybe_applied`.
- [ ] **Step 2: Run.** Expected: FAIL. **Step 3: Implement.** **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Checkpoint.**

### Task 6: Auth, plugin and registration

**Files:**
- Create: `youtube_music/auth.py`, `plugin.py`, tests `test_auth.py`, `test_plugin.py`
- Modify: `kit/oauth.py` (refresh sends `client_secret` when the credentials hold one)
- Modify: `pyproject.toml` entry point `youtube_music`, `uv sync`; `tests/test_layers.py`; `test_real_connectors.py` discovery; `kit/recording.py` `DEFAULT_REDACT` (`cookie`, `client_secret` already, `device_code`, `user_code`, `accountName`).

**Interfaces:**
- `auth.py`: `sapisid_from_cookie(cookie: str) -> str | None` (`__Secure-3PAPISID` accepted as a fallback); `sapisid_hash(sapisid: str, now: datetime) -> str` → `f"SAPISIDHASH {ts}_{sha1}"`; `class CookieAuth` (`AuthProvider`; `refresh()` returns `False`); `class GoogleAuth(PkceAuth)` with `TOKEN_URL = "https://oauth2.googleapis.com/token"`, display name "YouTube Music"; `start_device(client, client_id) -> DeviceCodeStart`; `poll_device(client, creds, keep, clock) -> DevicePoll`.
- `plugin.py`: `AUTH_MODE_ENV = "LINERSYNC_YTMUSIC_AUTH"`, `YTAuthMode(StrEnum): HEADERS = "headers"; OAUTH = "oauth"`, `auth_mode_from_env()`, `YouTubeMusicPlugin(mode)` implementing `ConnectorPlugin` and (OAuth mode) `DeviceCodeSetup`; `PLUGIN = YouTubeMusicPlugin(auth_mode_from_env())`.
  - Headers guide: INFO `before` ("unofficial; uses your signed-in browser session; may break when YouTube changes its site"), FORM `cookie` (MULTILINE_SECRET, label "Cookie header", help: how to copy it from DevTools → Network → a `music.youtube.com` request), TEST.
  - OAuth guide: INFO `before`, INFO `create_client` (Google Cloud console, "TVs and Limited Input devices" client, enable YouTube Data API v3), FORM `client_id` (TEXT, pattern `[0-9a-z-]+\.apps\.googleusercontent\.com`) + `client_secret` (SECRET), DEVICE_CODE `sign_in`, TEST.
  - `validate`: also rejects a cookie without SAPISID (exact copy in Review Focus 3).
  - `test_connection`: `account/account_menu`; no account name → `ConnectionTest(False, "Sign in to YouTube Music again")`. On success in headers mode, saves `cookie_seen_at` the first time a given cookie (by SHA-256) passes.
  - `login_expires_at`: headers → `cookie_seen_at + 30 days`; OAuth → `None`.

- [ ] **Step 1: Write the failing tests:** `sapisid_hash` against a fixed vector (compute with `hashlib` in the test from the documented formula); cookie parsing incl. the `__Secure-3PAPISID` fallback; `test_validate_rejects_cookie_without_sapisid`; both guides pass `check_guide`; device flow start/poll against the sim (pending → slow_down raises interval → done saves tokens); OAuth refresh sends `client_secret`; connection test ok / signed out; `login_expires_at` rules; plugin shape per mode.
- [ ] **Step 2: Run.** Expected: FAIL. **Step 3: Implement + registration.** **Step 4: Run** `uv sync && uv run pytest tests/adapters/connectors tests/test_layers.py tests/integration -v`. Expected: PASS.
- [ ] **Step 5: Checkpoint.**

### Task 7: Contract harness, integration, live wiring and docs

**Files:**
- Modify: `tests/contracts/harnesses.py` (`YouTubeMusicHarness`: no ISRCs in the catalog, all four faults), `tests/integration/test_real_connectors.py` (`test_spotify_to_youtube_music_matches_by_isrc_search`: the Spotify source's ISRCs map to the YouTube videos via the sim's `isrcs`, and the run matches all three without attention).
- Modify: `tests/live/conftest.py` (`"youtube_music"`: `YTMUSIC_COOKIE` in headers mode; `YTMUSIC_CLIENT_ID`, `YTMUSIC_CLIENT_SECRET`, `YTMUSIC_REFRESH_TOKEN` in OAuth mode), `tests/live/test_live_contract.py` (setup, missing id `"PL" + "0" * 32`, params), `devtools/live_login.py` (`youtube-music --client-id --client-secret`: prints the code and URL, polls, prints exports).
- Docs: `docs/connectors.md` §2 and §9 (YT Music checklist: every "From ytmusicapi" fact above; an ISRC search still finds the Art Track; a bogus ISRC gives an unrelated hit that is *not* floored; cookie expiry behaviour), `docs/server.md` (`LINERSYNC_YTMUSIC_AUTH`), `README.md`.

- [ ] **Step 1: Write the harness and the integration test; run** `uv run pytest tests/contracts tests/integration -v`. Expected: read/write laws PASS for youtube_music, the ISRC law SKIPs, the metadata law PASSes.
- [ ] **Step 2: Live wiring, live_login (with a test for the subcommand parse), docs.**
- [ ] **Step 3: Full gate**, backend and `npm run check`. Expected: all pass.
- [ ] **Step 4: Checkpoint.** (No live run: the user is away from the machine.)
