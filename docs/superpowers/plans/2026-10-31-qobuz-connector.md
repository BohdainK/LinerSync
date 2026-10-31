# Qobuz Connector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A read + write Qobuz connector, signed in by pasting the web player's
app ID and user auth token, shipped behind a visible "Experimental" label.

**Architecture:** Same package shape as Tidal (`parse.py`, `auth.py`,
`connector.py`, `plugin.py`) over the web player's private JSON API at
`https://www.qobuz.com/api.json/0.2`. Every request carries `X-App-Id` and
`X-User-Auth-Token`. A generic `experimental` plugin attribute flows through
`ConnectionInfo` → `ConnectionOut` → `ServiceGrid` badge.

**Tech Stack:** Python 3.13, httpx (`HttpClient`), pytest, React + Vitest.

**Spec:** `docs/superpowers/specs/2026-10-28-new-connectors-design.md` (§ Qobuz,
§ Per-connector checklist).

## Global Constraints

- Sign-in decided with the user (2026-10-31): **token paste only**. No email +
  password login, no scraping of the web player bundle: the user copies the
  `X-App-Id` and `X-User-Auth-Token` request headers from DevTools on
  play.qobuz.com. Personal use.
- No live calls to Qobuz while building (user is away from the PC; a live
  call was also refused by the safety classifier). Fixtures are hand-shaped
  from the documented community clients and marked as such.
- Capabilities: `READ_ORDER, ADD, REMOVE, CREATE_PLAYLIST, EDIT_METADATA`. No
  `REORDER` (spec: "only if the builder confirms a working endpoint" — not
  confirmable without live access). No `DELETE_PLAYLIST` (not in the spec).
- Experimental: `experimental: bool` on plugins (default `False` when absent),
  exposed on `ConnectionOut`, `ServiceGrid` shows an "Experimental" badge, and
  the guide's INFO step says the access is unofficial, may break when Qobuz
  updates its web player, and is against Qobuz's terms.
- No third-party SDK; all HTTP through `HttpClient`.
- Never commit (user's CLAUDE.md).

## Review Focus

- A token Qobuz no longer accepts → connection test says "Sign in to Qobuz
  again", reads raise `ReadFailed(auth_required=True)`; never an empty list.
- A playlist changing during a paged read (a song inserted or removed before
  the next offset) → `ReadFailed`, not a shifted or short snapshot.
- Removing one copy of a repeated song removes exactly that occurrence
  (`playlist_track_id`), and refuses when the fresh read no longer matches.
- Adds keep repeats (`no_duplicate=false`); a timeout on a non-idempotent add
  reports maybe-applied.
- Someone else's (subscribed) playlist is listed as not owned.

---

### Task 1: Experimental flag end to end

**Files:**
- Modify: `backend/src/linersync/adapters/connectors/kit/plugin.py` (add `is_experimental(plugin) -> bool`, documented optional attribute)
- Modify: `backend/src/linersync/server/connections.py` (`ConnectionInfo.experimental: bool`)
- Modify: `backend/src/linersync/server/api/schemas.py` (`ConnectionOut.experimental: bool`)
- Modify: `frontend/src/features/connections/ServiceGrid.tsx`, `frontend/src/test/fixtures.ts`, `frontend/openapi.json`, `frontend/src/api/schema.d.ts`
- Test: `backend/tests/server/test_connections.py`, `frontend/src/features/connections/connections.test.tsx`

- [ ] Test: a plugin with `experimental = True` lists with `experimental is True`; existing plugins list `False`.
- [ ] Test (frontend): a service with `experimental: true` shows an "Experimental" badge in its grid button; others don't.
- [ ] Implement; regenerate `openapi.json` + `schema.d.ts`; `npm run api:check`.

### Task 2: Parsing + fixtures

**Files:** Create `qobuz/parse.py`, `tests/fixtures/qobuz/{playlist_get,user_playlists,track_search,playlist_create}.json`, `tests/adapters/connectors/qobuz/test_parse.py`.

**Produces:** `SERVICE`, `API_BASE`, `parse_track(item, artwork_hash=None) -> Track`,
`artwork_url(item) -> str | None`, `parse_playlist_meta(obj, me: str | None) -> PlaylistMeta`,
`RawItem(playlist_track_id, track_id, added_at, item)`, `parse_tracks_page(obj) -> tuple[list[RawItem], int | None]`,
`parse_account(body) -> tuple[str | None, str | None]` (user id, login).

- [ ] Tests: title joins `version` as "Title (Version)"; artists = performer then other MainArtist/FeaturedArtist names from `performers`; duration s → ms; ISRC carried; url `https://open.qobuz.com/track/<id>`; owned iff owner id == me (unknown me → owned); `version_token` is None; fixture key-path shape tests.

### Task 3: Auth, simulator, connector

**Files:** Create `qobuz/auth.py` (`QobuzAuth`), `qobuz/connector.py`, `tests/adapters/connectors/qobuz/{sim.py,test_auth.py,test_connector.py}`.

- `LIMITS = ConnectorLimits(page_size=500, add_batch_size=50, search_cost=2)`.
- Reads: `GET playlist/getUserPlaylists` (offset/limit 500) and `GET playlist/get`
  (`extra=tracks`, offset/limit). Paging by `offset += len(items)`; an empty
  page before `total` → `ReadFailed`; more than `total` → `ReadFailed`; a
  repeated `playlist_track_id` → `ReadFailed`; after paging, re-fetch with
  `limit=1` and compare `tracks_count` and `updated_at` → `ReadFailed` on change.
  404 → `PlaylistNotFound`.
- Search: `GET track/search?query=<isrc>`; hits whose ISRC equals the query win;
  otherwise `query="<artist> <title>"`; at most 10.
- Writes (form POST): `playlist/create` (`is_public=false`,
  `is_collaborative=false`, not idempotent), `playlist/update` (idempotent),
  `playlist/addTracks` (`track_ids` comma list, `no_duplicate=false`, batches
  of 50, not idempotent), `playlist/deleteTracks` (`playlist_track_ids` from a
  fresh read, checked against `Removal.position`/`track_id`, not idempotent).
- [ ] Contract-shaped tests per behaviour above, RED first.

### Task 4: Plugin, registration, docs

**Files:** Create `qobuz/plugin.py`, `tests/adapters/connectors/qobuz/test_plugin.py`. Modify `pyproject.toml`, `tests/test_layers.py`, `tests/contracts/harnesses.py`, `tests/integration/test_real_connectors.py`, `tests/live/{conftest.py,test_live_contract.py}`, `kit/recording.py` (`user_auth_token`, `login`), `docs/connectors.md`, `docs/server.md`, `README.md`.

- Guide: INFO (experimental warning) → FORM (`app_id` TEXT pattern `\d{6,12}`, `user_auth_token` SECRET, with DevTools instructions) → TEST.
- `test_connection`: `playlist/getUserPlaylists?limit=1`, stores `user_id`, account name = login; 401 → "Sign in to Qobuz again"; 400 → "Qobuz refused the app ID".
- `login_expires_at` → None. `experimental = True`.
- [ ] `check_guide == []`, contract suite over the Qobuz harness, discovery assertion, full gates.
