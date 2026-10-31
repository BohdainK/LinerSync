# New connectors: Jellyfin, Tidal, YouTube Music, Qobuz

Status: draft for review. Answers folded in, YT Music identity strategy researched.

## Intent

Add four new services alongside Spotify and Apple Music. Jellyfin is
self-hosted and is **read-only** — a source you copy *from*, never a
destination. Tidal, YouTube Music and Qobuz are **read and write**, on par with
the existing streaming connectors.

## Goals

- Each service ships as its own connector package under
  `backend/src/linersync/adapters/connectors/<svc>/`, discovered through the
  `linersync.connectors` entry point, and passes the contract suite
  (`testing/contract.py`) for the capabilities it declares.
- Jellyfin can be used as a source for syncs and transfers, and the app makes it
  impossible (and clearly explained) to pick it as a destination.
- Tidal, YouTube Music and Qobuz can be both source and destination.

## Non-goals

- Liked songs / favourites / saved albums / followed artists **in this
  project**. The long-term goal is to sync everything, but that needs a
  domain and protocol change (library collections alongside playlists) that
  should be designed once, for all six services, as its own project. These
  connectors must not make that harder: keep the auth/HTTP/parse layers
  separate from the playlist-specific `connector.py`, and request OAuth
  scopes that already cover library access where it costs nothing extra
  (Tidal `collection.read collection.write`; YT Music's auth covers it
  already) so users don't have to re-authorise later.
- Writing to Jellyfin.
- ISRC enrichment via MusicBrainz for Jellyfin (possible follow-up; see below).
- Third-party SDKs (`ytmusicapi`, `tidalapi`, `jellyfin-apiclient-python`).
  They bypass `kit/http.py`'s `HttpClient` and the injected `ctx.transport`
  that the simulators and contract suite depend on, and two of them are
  GPL/LGPL.

## Anti-goals

- A connector that silently returns an empty or partial playlist on failure
  (must raise `ReadFailed`, per `docs/connectors.md` §5).
- A read-only service appearing in a destination picker, or a sync to it
  that "succeeds" by recording every add as "change not made".
- Shipping a fragile connector (Qobuz, YT Music) in a way that makes the stable
  ones look broken — failures must surface as auth/attention states, not
  crashes or generic errors.

## Constraints from the existing code

- Protocol: `ports/connector.py` — `list_playlists`, `read_playlist`, `search`,
  `create_playlist`, `apply`; plus `capabilities` and `ConnectorLimits`.
- Plugin: `adapters/connectors/kit/plugin.py` — `ConnectorPlugin` with a
  data-driven setup guide; optional `OAuthSetup` / `BrowserSignInSetup`.
- All HTTP through `kit/http.py` `HttpClient` (rate limit, retries,
  Retry-After, one refresh on 401). Separate unauthenticated client for
  artwork; artwork hashed via `ArtworkHasher`.
- Credentials: `SqliteCredentials`, one encrypted flat `dict[str,str]` per
  service. New secret keys must be added to `RecordingTransport`'s
  `DEFAULT_REDACT`.
- Layer rules: `backend/tests/test_layers.py` `FORBIDDEN` map needs an entry
  per new package.
- Matching (`domain/matching/scoring.py`): ISRC is the strongest signal; title,
  artists, duration and artwork hash carry the rest. Connectors should fill
  every `Track` field they can.

## Per-service design

### Jellyfin (read-only) — risk: low

- Category `self_hosted`. Capabilities `{READ_ORDER}` only (follow
  `application/backup_source.py`: write methods raise `WriteFailed`).
- Setup guide (form only, decided: API key): server URL, API key (SECRET),
  and the Jellyfin username whose playlists to read. `validate`/
  `test_connection` resolve the username to a user id via `GET /Users`
  and store it alongside the key. Every per-user call passes `userId`
  explicitly. Header `Authorization: MediaBrowser Token="…"`.
  `login_expires_at` returns `None` (API keys don't expire).
- Reads: playlists via `GET /Items?userId=…&IncludeItemTypes=Playlist&Recursive=true`;
  tracks via `GET /Playlists/{id}/Items` with `Fields=ProviderIds,MediaSources`,
  paginated with `StartIndex`/`Limit`, total from `TotalRecordCount`.
- `search`: metadata search over `IncludeItemTypes=Audio` (`SearchTerm`). There
  is no ISRC in Jellyfin metadata, so `Track.isrc` is `None`.
- `test_connection`: `GET /Users/Me` (or `/System/Info` for reachability vs.
  auth distinction).
- Follow-up (not in scope): resolve `MusicBrainzRecording` → ISRC through
  the MusicBrainz API to improve match quality.

### Tidal (read + write) — risk: low–medium

- Official v2 API (`openapi.tidal.com/v2`, JSON:API). Raw httpx, modelled on
  the published OpenAPI spec.
- Auth: OAuth authorization code + PKCE at `login.tidal.com`, same guide
  shape as Spotify (INFO → FORM `client_id` → OAUTH → TEST). Each self-hoster
  registers their own free app on developer.tidal.com because the redirect URI
  must be registered (same model as Spotify today). Scopes:
  `playlists.read playlists.write search.read user.read collection.read
  collection.write` (collection scopes requested now for the future
  library-sync project).
- Capabilities: target everything Spotify declares (all except
  `DELETE_PLAYLIST`): add, remove, reorder (PATCH on
  `/playlists/{id}/relationships/items`), metadata, create. Builder confirms
  each against the live API and drops any that don't hold.
- `search`: `GET /tracks?filter[isrc]=…` first, then `/searchResults`.
- Optimistic concurrency: use any playlist version/etag the API exposes as
  `version_token`; otherwise check-before/check-after like Spotify.
- `devtools/live_login.py`: `tidal` subcommand reusing the Spotify loopback
  PKCE helper.

### YouTube Music (read + write) — risk: medium–high

- No official API. Talk to the internal `youtubei/v1` endpoints directly via
  httpx, porting request shapes and response parsing from ytmusicapi (MIT;
  credit in the module docstring). Isolate all response-shape parsing in
  `parse.py` with recorded fixtures so breakage is a fixture update.
- Auth (decided: both, user picks during setup):
  - **OAuth device code** with the self-hoster's own Google Cloud client
    ("TVs and Limited Input devices" type): FORM `client_id` + `client_secret`,
    then a device-code step showing the code and verification URL, polling
    until approved. The setup guide has no device-code step type today; add a
    generic `DEVICE_CODE` step kind (backend guide schema + frontend
    `SetupStepper` renderer) rather than special-casing YT Music, since Tidal
    or others may reuse it.
  - **Browser headers**: SECRET FORM field for the request headers/cookie
    copied from a logged-in music.youtube.com tab (same pattern as Apple
    Music's web mode). The connector derives the SAPISIDHASH auth header from
    the cookie.
  - Mode is chosen like Apple Music's (`LINERSYNC_YTMUSIC_AUTH` env var
    picking one guide) *or* as a choice inside one guide — builder's call;
    prefer an in-UI choice if the guide schema can express it cheaply.
- Capabilities: `READ_ORDER, ADD, REMOVE, REORDER, EDIT_METADATA,
  CREATE_PLAYLIST` (all supported by the endpoints ytmusicapi uses). Removal
  needs per-entry `setVideoId`, which the connector keeps internally.
- **Identity without ISRC** (researched 2026-10-28). YTM never *returns*
  ISRCs, but its search *indexes* them: a raw ISRC as the query (songs
  filter) returned the correct Art Track in both live tests. A bogus ISRC
  also returned an (unrelated) song, so a hit is only a candidate. Odesli
  (public API deprecated, 401) and MusicBrainz YouTube URL relations (sparse)
  are not usable. In scope:
  - `search`: if the query has an ISRC, search YTM with the ISRC string
    first, then fall back to `"<artist> <title>"`. Return up to 10 song
    candidates. `Track.isrc` stays `None`: never copy the query ISRC onto a
    candidate, because that would trigger the 0.97 equal-ISRC floor on a
    possible false hit.
  - Prefer Art Tracks: read `musicVideoType` and drop or down-rank
    OMV/UGC results (video intros and outros skew duration) when an ATV
    candidate exists.
  - Matching: add an "ISRC search hit" signal to `domain/matching`. A
    candidate that came back from an ISRC query **and** agrees on title,
    artists and duration (±2 s) gets confidence floored at about 0.95, just
    under a real ISRC match. Without that agreement the hit adds nothing.
    The builder chooses how the connector marks the hit (e.g. an optional
    field on `Track` or a search-result wrapper), keeping
    `domain/matching` free of service names.
  - Cache verified videoId↔ISRC pairs alongside the existing sync pairings,
    so repeat syncs skip the search.
  - Follow-ups (not in scope): parse the Art Track description (`WEB`
    player `shortDescription`: label, ℗ year, release date) to improve
    YTM-as-source matching with an ISRC round-trip check; match by album
    (YTM album + track position) for whole-album transfers.
  - This behaviour is undocumented: unit-test it against recorded fixtures,
    and add a live check to the YT Music live checklist so breakage is
    noticed.
- `login_expires_at`: for header auth, mark short (cookie lifetime) so the
  attention system nudges re-auth; for OAuth, refresh-token based.

### Qobuz (read + write) — risk: high

- No obtainable official API credentials. Same approach as streamrip /
  qobuz-dl: scrape `app_id` and secret from the web player bundle, cached in
  credentials and re-scraped on auth failure (precedent:
  `apple_music/web_token.py`).
- Auth: email + password login (`user/login`) → `user_auth_token`; store the
  token and the `app_id` it was issued under, not the password. Alternative:
  paste `user_auth_token` from the web player.
- Capabilities: `READ_ORDER, ADD, REMOVE, CREATE_PLAYLIST, EDIT_METADATA`;
  `REORDER` only if the builder confirms a working endpoint.
- `search`: Qobuz tracks carry `isrc`; search with the ISRC as the query
  first, then metadata.
- Ships **behind an "experimental" label** (decided). Add an
  `experimental: bool` attribute to `ConnectorPlugin` (default `False`),
  expose it on `ConnectionOut`, and have `ServiceGrid` show an
  "Experimental" badge plus a one-line warning in the setup guide INFO step
  (unofficial access, may break when Qobuz updates its web player, against
  Qobuz's terms). Not hidden behind a flag — visible but clearly labelled.

## Cross-cutting changes

1. **Read-only destinations.** Backend: reject a sync/transfer whose
   destination lacks `ADD` in `server/api/syncs.py` (and the transfer
   endpoint) with a clear error; add a "can't add tracks" message to
   `domain/capabilities.py:limitations`. Frontend: filter `ServiceSelect`
   (`features/pickers/ServiceSelect.tsx`) to services with `add` when used as a
   destination (`NewSyncPage.tsx`, `NewTransferPage.tsx`); mirror the
   limitation text in `frontend/src/lib/limitations.ts`.
2. **ISRC-less connectors in the contract suite.** Jellyfin and YT Music
   can't satisfy `search_by_isrc_finds_track` or an ISRC-bearing catalog.
   Make that law conditional (e.g. a harness flag `has_isrc`, or
   a `search_by_metadata_finds_track` law that runs for those harnesses
   instead), and relax the catalog's ISRC requirement for them. The builder
   decides the mechanism; the laws must still prove search works.
3. **Live tests.** Generalise `tests/live/test_live_contract.py`
   (`LiveHarness` per-service credential wiring, `_MISSING_PLAYLIST_ID`,
   fixture params) into a per-service table rather than more if/else. For
   Jellyfin, `make_playlist` can't create playlists through the connector:
   use a pre-arranged playlist named via env var, or create it through
   the Jellyfin API directly in the harness.

## Per-connector checklist (from the existing pattern)

- Package with `parse.py`, `auth.py`, `connector.py`, `plugin.py`.
- Entry point in `backend/pyproject.toml`, then `uv sync`.
- Layer rules entry in `backend/tests/test_layers.py`.
- Simulator (`httpx.MockTransport`, same fault hooks as the Spotify sim),
  fixtures in `backend/tests/fixtures/<svc>/`, shape tests, unit tests
  (`test_parse`, `test_auth`, `test_plugin` asserting `check_guide == []`,
  `test_connector`).
- Harness + `HARNESSES` entry in `backend/tests/contracts/harnesses.py`.
- Discovery assertion in `backend/tests/integration/test_real_connectors.py`.
- Live wiring (`LIVE_ENV`, harness table) and `live_login` subcommand where
  auth is interactive.
- `RecordingTransport.DEFAULT_REDACT` for new secret keys.
- Docs: `docs/connectors.md` §2 and §9 (env vars + live checklist),
  `docs/server.md` (env vars, OAuth redirect notes), README prerequisites
  and features.

## Delivery order (my call)

One connector per change, lowest risk first so the cross-cutting work lands
on the easiest case:

1. Read-only destination gating + ISRC-less contract support, delivered with
   **Jellyfin**.
2. **Tidal**.
3. **YouTube Music**.
4. **Qobuz**.

## Calls I made (check these)

- Library sync deferred to its own project; this one only keeps the door open.
- Jellyfin user chosen by username, resolved to an id at setup.
- Generic `DEVICE_CODE` setup step rather than a YT-Music-only one.
- Qobuz "experimental" is a visible badge + warning, not a hidden flag.
- Raw httpx for all four, no SDKs; port ytmusicapi knowledge with credit.
- Tidal: official v2 API only, not the private v1 API that `tidalapi` uses;
  each self-hoster registers their own app.
- Jellyfin: no MusicBrainz ISRC enrichment in v1.
- Destination gating enforced both server-side and in the UI.

## Left to the builder

- Exact contract-suite mechanism for ISRC-less connectors.
- `ConnectorLimits` per service (page and batch sizes from each API).
- Which capabilities each write connector finally declares, after checking
  against the live API.

- YT Music: ISRC-search signal floor at ~0.95 and Art Track preference in
  scope; description parsing and album matching deferred.

## Decisions from review (2026-10-28)

- Qobuz: ship behind an experimental label.
- YT Music: support both OAuth (own Google Cloud client) and browser headers.
- Jellyfin: API key, not username/password.
- Long-term: sync everything (library, not just playlists), as a later
  project.
