# Writing a connector

A connector teaches LinerSync to talk to one music service. This guide is for a
contributor who has never seen the code. Paths are relative to `backend/src/linersync/`
unless noted. Everything here is written from this codebase and the services' official
documentation.

## 1. What a connector is

A connector is an object that satisfies the `Connector` protocol
(`ports/connector.py`). It is the only thing the sync engine knows about a service:

```python
class Connector(Protocol):
    service: ServiceId                    # "spotify", "apple_music", ...
    display_name: str                     # "Spotify"
    capabilities: frozenset[Capability]
    limits: ConnectorLimits

    async def list_playlists(self) -> list[PlaylistMeta]: ...
    async def read_playlist(self, playlist_id: str) -> PlaylistSnapshot: ...
    async def search(self, query: TrackQuery) -> list[Track]: ...
    async def create_playlist(self, draft: PlaylistDraft) -> PlaylistMeta: ...
    async def apply(self, playlist_id: str,
                    op: UpdateMetadata | AddTracks | Reorder | RemoveTracks) -> None: ...
```

**Capabilities** (`domain/capabilities.py`) say what the service can do: `READ_ORDER`,
`ADD`, `REMOVE`, `REORDER`, `EDIT_METADATA`, `CREATE_PLAYLIST`, `DELETE_PLAYLIST`.
The planner reads them and degrades honestly. For example, Apple Music's public API can
only add, so it declares `{READ_ORDER, ADD, CREATE_PLAYLIST}` and syncs into Apple run
as add only. Declare a capability only if `apply` really implements it, and do not
declare one the service cannot do. The contract suite (section 7) checks both.

**`ConnectorLimits(page_size, add_batch_size, search_cost=1)`** (`ports/connector.py`):
the largest page you can read, the largest batch you can add in one request, and how
many quota units one search costs. Spotify is `ConnectorLimits(50, 100, 2)`, Apple Music
is `ConnectorLimits(100, 100, 2)`. The engine uses them to plan; the connector must
itself split work that exceeds a limit (see `apply_in_batches`, section 6).

Track ids are whatever is stable on your service and valid for `AddTracks`. Spotify uses
the URI (`spotify:track:...`); Apple uses the catalog id.

## 2. Package layout and entry-point registration

```
adapters/connectors/
  kit/               shared helpers; imports no service package
  spotify/           one package per service
    auth.py          sign-in and token handling
    connector.py     the Connector implementation
    parse.py         JSON -> domain objects, pure functions
    plugin.py        the ConnectorPlugin and the PLUGIN constant
  apple_music/       same shape
```

Layer rules (enforced by `tests/test_layers.py`): `kit` must not import a service
package, and a service package must not import another service package or
`linersync.application`. Keep parsing in pure functions in `parse.py` so they are easy
to test against fixtures.

LinerSync finds connectors through the Python entry-point group `linersync.connectors`
(`kit/discovery.py`). Built-in connectors are registered in `backend/pyproject.toml`:

```toml
[project.entry-points."linersync.connectors"]
spotify = "linersync.adapters.connectors.spotify.plugin:PLUGIN"
apple_music = "linersync.adapters.connectors.apple_music.plugin:PLUGIN"
```

A third-party package registers the same way in its own `pyproject.toml`. The value
points to a plugin instance (or a class that takes no arguments, which is instantiated):

```toml
[project]
name = "linersync-notely"

[project.entry-points."linersync.connectors"]
notely = "linersync_notely.plugin:PLUGIN"
```

Run `uv sync` (or reinstall the package) after changing entry points, because they are
read from installed metadata. `discover_plugins()` never raises for a broken plugin. A
plugin is reported in `PluginRegistry.errors` instead when it:

- fails to import,
- is not a `ConnectorPlugin`,
- has a setup guide that fails `check_guide`, or
- declares a service id that another plugin already provides (the first one by entry-point
  name wins).

## 3. `ConnectorPlugin`, `OAuthSetup`, `BrowserSignInSetup`

`ConnectorPlugin` (`kit/plugin.py`) is what the application sees. It bundles metadata,
setup, a connection test and a factory:

| Member | Purpose |
|---|---|
| `service`, `display_name`, `category` | Identity. `category` is `"streaming"` or `"self_hosted"`. |
| `capabilities`, `limits` | Same values the connector exposes. |
| `quota_policy` | `QuotaPolicy(daily_limit, reset_timezone)`. `QuotaPolicy()` means no daily cap. |
| `setup_guide()` | The guided setup, as data (section 4). |
| `validate(values)` | `validate_values` plus service-specific checks. Returns `{field_key: message}`. |
| `test_connection(ctx)` | Returns `ConnectionTest(ok, message, account_name)`. Must not raise for service errors. |
| `login_expires_at(credentials)` | When the stored login stops working, or `None`. |
| `create(ctx)` | Builds the `Connector`. |

`ConnectorContext` carries what a plugin needs and nothing global: a `CredentialStore`
(`ports/credentials.py`), a `QuotaRecorder` (`ports/quota.py`), a `Clock`,
`ConnectorSettings`, an optional httpx `transport` (tests pass a simulator here) and an
async `sleep`. Always build HTTP clients from `ctx.transport` and `ctx.sleep` so tests
stay offline and fast. Credentials are stored per service as flat `dict[str, str]`;
`save` merges keys.

Two optional protocols describe how a login is obtained. A plugin implements the one
that fits; both are `runtime_checkable`.

**`OAuthSetup`**: for redirect-based sign-in (Spotify, Authorization Code with PKCE).

- `authorize(credentials, redirect_uri, state) -> AuthorizeRequest(url, keep)`. `keep`
  is data the server stores until the callback (Spotify keeps the PKCE `code_verifier`).
- `complete(ctx, redirect_uri, code, keep)` exchanges the code and saves tokens.

**`BrowserSignInSetup`**: for sign-in that runs JavaScript in the user's browser (Apple
MusicKit).

- `sign_in_config(credentials) -> dict[str, str]` gives the page what it needs (Apple
  returns a freshly signed developer token).
- `accept_sign_in(ctx, result)` stores what the page returns, and raises `ValueError`
  on bad input.

A service whose setup is only form fields (an API key, a server URL) implements neither.

## 4. Setup guides as data

The UI renders `SetupGuide` (`ports/setup.py`), so a plugin never builds screens. A guide
is a tuple of `SetupStep`s; each has a `StepKind` (`INFO`, `FORM`, `OAUTH`,
`BROWSER_SIGN_IN`, `TEST`) and optional `body`, `fields`, `links`, `screenshots` and
`copy_values`. Text may use `{placeholders}` (for example `{redirect_uri}`) that
`render_guide(guide, context)` fills in; an unknown placeholder raises `ValueError`.

**Guide placeholders.** The server renders every guide with exactly two keys:
`{redirect_uri}` (the OAuth callback, `<public URL>/api/connections/oauth/callback`)
and `{public_url}` (the server's public URL, without a trailing slash). They are the
only keys; any other placeholder makes the guide fail to render. Placeholders are
filled in step bodies, link URLs and copy values. A literal brace is written `{{`
(and `}}`), as in Python's `str.format`.

Screenshots are package-relative resources with `Annotation` regions given as 0..1
fractions of the image. `SetupField` has a `key`, `label`, `kind` (`TEXT`, `SECRET`,
`MULTILINE_SECRET`), an optional `pattern` (a full match regex) with a `pattern_hint`
shown on mismatch, and `required`.

A small complete example (a self-hosted "Notely" server needing a URL and an API key):

```python
from collections.abc import Mapping

from linersync.ports.setup import (
    FieldKind, Link, SetupField, SetupGuide, SetupStep, StepKind,
    check_guide, validate_values,
)


def notely_guide() -> SetupGuide:
    return SetupGuide(
        service="notely",                      # a ServiceId: [a-z][a-z0-9_]*
        display_name="Notely",
        category="self_hosted",
        prerequisites=("A running Notely server",),
        steps=(
            SetupStep(
                "key", "Create an API key", StepKind.INFO,
                body="In Notely open Settings, API keys, and create one.",
                links=(Link("Notely docs", "https://notely.example/docs/api-keys"),),
            ),
            SetupStep(
                "details", "Enter your details", StepKind.FORM,
                fields=(
                    SetupField("base_url", "Server URL",
                               pattern=r"https?://\S+",
                               pattern_hint="Start with http:// or https://"),
                    SetupField("api_key", "API key", kind=FieldKind.SECRET),
                ),
            ),
            SetupStep("test", "Test the connection", StepKind.TEST),  # always last
        ),
    )


assert check_guide(notely_guide()) == []          # [] means the guide is well formed


def validate(values: Mapping[str, str]) -> dict[str, str]:
    return validate_values(notely_guide(), values)   # {} when every field is fine
```

`check_guide` returns a list of problems and is also run by discovery, so a bad guide
disables the plugin. It reports:

- an invalid service id,
- duplicate step ids or field keys,
- an invalid regex,
- an annotation outside 0..1,
- a guide whose last step is not a `TEST` step.

Put a test next to your plugin that asserts `check_guide(plugin.setup_guide()) == []`.
Never put a secret in a guide: the plain-text fallback renders the same data.

## 5. The error rules

All connector errors derive from `ConnectorError(service, message, auth_required=...)`
(`ports/errors.py`). Messages must never contain secrets (tokens, keys, codes). The HTTP
layer already strips query strings and rebuilds token-endpoint failures; keep that
discipline in your own code.

- **A failed read is never "empty".** Any HTTP error, timeout, missing page, total that
  changes between pages, or playlist that changes during the read raises `ReadFailed`.
  The engine treats an empty snapshot as truth and may remove songs from the other side.
  If you cannot tell "no tracks" from "error", it is an error. (Apple's
  `/tracks` of an empty library playlist is documented nowhere, so a 404 there is
  `ReadFailed`; the live checklist re-examines it.)
- **`PlaylistNotFound` only when authoritative.** Raise it on a definite 404 for that
  playlist, or when the connector itself establishes that the user can no longer read it.
  Never for timeouts, 5xx, or "list came back without it".
- **`WriteFailed(maybe_applied=...)`.** `maybe_applied=False` means the write provably
  did not happen (a 4xx, a 429, a failure to connect). `maybe_applied=True` means it may
  have landed: a read timeout, a 5xx, or a failure after an earlier batch succeeded. On
  `True` the engine re-reads the playlist and replans, so a song that landed is not added
  again as long as the re-read shows it. If your service's reads lag behind its writes
  (Apple Music), wait for the change to show before raising: the Apple connector polls
  for up to its settle timeout (10 s) so that the engine's re-read sees the songs that
  landed. If the lag outlasts that wait, a song can still be added twice. When in
  doubt, say `True`. `HttpFailure.maybe_applied` already follows these rules, and
  `write_failed(e)` converts one into a `WriteFailed`.
- **A cover never blocks a sync.** If the source cover can't be fetched or prepared, the
  Spotify connector applies the name and description without it and does not retry the
  cover until the source cover changes.
- **`auth_required=True`** says that the user must sign in again (a 401 that a refresh
  could not fix, a revoked token, an expired login). The UI turns it into a "sign in
  again" prompt rather than a generic error. Set it from `HttpFailure.auth_required`; use
  `read_failed(e)` and `write_failed(e)` to carry it across.

## 6. Using `HttpClient`

Use the shared `HttpClient` (`kit/http.py`) for every request to the service. It gives
you rate limiting (`HttpSettings`: timeout 30 s, 5 requests/s, burst 10), retries with
back-off and `Retry-After` (`RetryPolicy`: 4 attempts, 0.5 s base, 30 s cap, at most 60 s
of `Retry-After`), quota accounting, and one refresh of credentials on a 401 through
an `AuthProvider` (`headers()` and `refresh()`). `request()` returns the response on 2xx
and raises `HttpFailure` otherwise.

```python
api = HttpClient(
    SERVICE, "https://api.example.com",
    settings=ctx.settings.http, quota=ctx.quota, quota_policy=POLICY,
    clock=ctx.clock, auth=my_auth, transport=ctx.transport, sleep=ctx.sleep,
)
response = await api.request("GET", "/things", idempotent=True, params={"limit": 50})
```

**Every request must say `idempotent=` explicitly.** It decides whether a retry is safe:

- A request that may have reached the server and changed something is **never** retried
  unless it is idempotent, or the response proves that it was rejected. Proof is a
  429 that is not a quota response, a failure to connect (the request was never sent), or
  whatever your `proven_rejection` hook accepts (default: only 429). A 503 is not proof
  unless the service documents that it is sent before processing.
- Idempotent means that sending it twice leaves the same state as sending it once. A
  `GET` is. So is `DELETE` of URIs and `PUT` of a name.

The Spotify reorder shows why this matters. A reorder is `PUT /playlists/{id}/items` with
`range_start`, `insert_before` and `range_length`. It moves a block of items *relative to
current positions*, so repeating it after it already landed moves things again and leaves a
different order. It is therefore `idempotent=False`: if it times out, the connector reports
`WriteFailed(maybe_applied=True)` instead of retrying, and the engine re-reads before
deciding. Setting `idempotent=True` on it would turn a timeout into silent corruption.

Other helpers:

- `is_quota_exceeded`: a hook that tells a quota-exhausting 429 apart from a rate-limiting
  one (Spotify checks the body's `reason == "QUOTA_EXCEEDED"`).
- `apply_in_batches(items, size, send)`: sends chunks in order and marks a failure after
  the first chunk as `maybe_applied`.
- `kit/images.py`: `ArtworkHasher` and `http_fetcher` for artwork signals. Use an
  *unauthenticated* client for artwork hosts so tokens never reach a CDN.
- `kit/recording.py`: `RecordingTransport` (section 9).

## 7. Testing

The default test run makes no network calls: every request goes through
`httpx.MockTransport`-style transports.

1. **Write a simulator.** A small in-memory fake of the service's HTTP API that exposes
   a `transport` and can add playlists, fail the next matching request
   (`fail_next(method, path_regex, status, times=, apply_first=)`), time out
   (`timeout_next`) and truncate pages (`truncate`). See
   `tests/adapters/connectors/spotify/sim.py` and `.../apple_music/sim.py`.
2. **Unit-test the pieces:** `parse.py` against fixtures, auth, the plugin guide, and the
   connector against the simulator.
3. **Add a harness** in `tests/contracts/harnesses.py`. A harness implements
   `ConnectorHarness` (`testing/contract.py`):
   - `connector`
   - `catalog`: at least 3 tracks with distinct titles, each with an ISRC
   - `make_playlist(name, track_ids)`: arranges a playlist out of band and returns its id
   - `supports(fault)`
   - `inject(fault, playlist_id)`

   The faults are `READ_ERROR`, `TRUNCATED_READ`, `WRITE_REJECTED` and
   `WRITE_MAYBE_APPLIED`. Register it in the `HARNESSES` dict.
4. **Run the laws.** `linersync.testing.contract.LAWS` is a mapping of named async checks
   that every connector must pass, such as `read_is_complete_ordered_with_repeats`,
   `remove_one_copy_of_repeat_keeps_other` and `read_error_is_never_empty`.
   `tests/contracts/test_connectors.py` runs every law against every harness. A law
   raises `ContractSkip` when your capabilities or harness lack what it needs, and it is
   then reported as skipped, not passed.
5. **Fixture shapes.** Real responses are stored under `tests/fixtures/<service>/`.
   `tests/adapters/connectors/shapes.py` offers `load_fixture(service, name)`, which
   prefers `tests/fixtures/<service>/recorded/<name>.json` (a real recorded response)
   over the hand-written `tests/fixtures/<service>/<name>.json`, and `key_paths(obj)`,
   which returns the dotted key paths of a JSON value (list items collapse to `[]`). The
   shape tests assert that the simulator's responses contain no key path that the real
   fixture lacks, so the simulator cannot invent fields. Drop a recorded response
   in and the same tests are checked against it.

Full check, from `backend/`:

```
uv run pytest -q && uv run mypy --strict src tests && uv run ruff check src tests
```

## 8. PR checklist

- [ ] The package follows the layout in section 2, and `tests/test_layers.py` passes.
- [ ] The entry point is registered and `uv sync` was run.
- [ ] Capabilities are exactly what `apply` implements; limits match the service.
- [ ] A failed read can never come back as an empty snapshot; `PlaylistNotFound` is raised
      only on an authoritative answer.
- [ ] Every `HttpClient.request` states `idempotent=`; a non-idempotent write reports
      `maybe_applied=True` when it may have landed.
- [ ] No secret appears in an exception message, an event, a log or a recording.
- [ ] `check_guide(plugin.setup_guide()) == []`, and `validate` has a test.
- [ ] A simulator, a harness and the `LAWS` run exist; the fixtures are in
      `tests/fixtures/<service>/`.
- [ ] The default run makes no network calls; any live test carries `@pytest.mark.live`.
- [ ] The full check passes with no output beyond success.
- [ ] Anything not verified against the real service is listed in the PR description.

## 9. Live verification (maintainers)

Simulators encode what the docs say. Before relying on a connector, a maintainer checks
the same behaviour against the real service. Live tests are opt-in: they carry
`@pytest.mark.live`, and `addopts = "-m 'not live'"` keeps them out of the default run.

**Sign in.** The helper serves only on 127.0.0.1, finishes one sign-in, prints `export`
lines and exits:

```
uv run python -m linersync.devtools.live_login spotify --client-id ID [--port 8765]
uv run python -m linersync.devtools.live_login apple-music --team-id T --key-id K \
    --key-file PATH [--port 8766]
```

For Spotify, add `http://127.0.0.1:8765/callback` as a Redirect URI of your app first. For
Apple the sign-in page loads MusicKit JS from `js-cdn.music.apple.com` and posts the Music
User Token back to the local server. Paste the printed `export` lines into your shell.

**Run.** The tests skip unless `LINERSYNC_LIVE=1` and the service's variables are set
(`tests/live/conftest.py: live_skip_reason`):

- Spotify: `SPOTIFY_CLIENT_ID`, `SPOTIFY_REFRESH_TOKEN`
- Apple: `APPLE_TEAM_ID`, `APPLE_KEY_ID`, `APPLE_PRIVATE_KEY_PATH`, `APPLE_MUSIC_USER_TOKEN`

```
uv run pytest -m live -q
```

`LiveHarness` builds the real connector over a `RecordingTransport`, so every exchange is
saved with secrets redacted under `tests/fixtures/<service>/recorded/raw/`. It finds three
well-known songs by search, creates playlists named `LinerSync live test <timestamp>`, and
runs every law in `LAWS`. It injects no faults, so fault laws are skipped. Review recordings
before sharing: redaction covers known secret keys, not everything a service might put in a
body.

### Checklist

**Spotify**

- [ ] `DELETE /items` removes every copy of a repeated URI.
- [ ] The `PUT /items` reorder indexes (`range_start`, `insert_before`) behave as the
      connector assumes.
- [ ] `POST /items` with `position` inserts where expected.
- [ ] The `/items` page limit is 50.
- [ ] The `fields=...items(total)` syntax is accepted and returns the total.
- [ ] Description escaping and newlines round-trip (Spotify HTML-escapes text, for example
      `&amp;`).
- [ ] The mosaic cover host (`mosaic.scdn.co`) is what auto-generated covers use.
- [ ] Refresh-token rotation under PKCE: whether a refresh returns a new refresh token, and
      the connector saves it.
- [ ] The `invalid_grant` shape after 6 months (from the docs), so the connector asks for a
      new sign-in.
- [ ] The 429 body `reason` and the `Retry-After` header.
- [ ] `external_ids.isrc` is present in items and in search results.
- [ ] Deleting a playlist only unfollows it, and `GET /playlists/{id}` still answers.
      Check the 2026 response of `GET /me/library/contains?uris=spotify:playlist:<id>`,
      then implement unfollow detection as `PlaylistNotFound` (Rebuild needs it).

**Apple Music**

- [ ] The paid Apple Developer Program membership is active.
- [ ] **What an empty library playlist's `/tracks` returns.** If emptiness can be told apart
      from an error, revise decision D3 (today a 404 is `ReadFailed`, never empty).
- [ ] Library lag after create and add, measured against the 10 s settle (polling every 1 s).
- [ ] The `POST /tracks` batch limit.
- [ ] The error status for a playlist with `canEdit: false`.
- [ ] `lastModifiedDate` is available.
- [ ] The `ids=` batch limit for catalog lookups.
- [ ] The Music User Token lifetime (the code assumes 180 days as an estimate).
- [ ] After adding catalog id X, the library song reports `playParams.catalogId == X`. If
      Apple maps it to another id, every add-only run would add the song again.
- [ ] `lastModifiedDate` stays the same across two reads with no edits in between (the
      changed-during-read check, R10, depends on it).
- [ ] Playlist artwork URLs stay the same across reads. Otherwise the planner sees a new
      cover every run and plans `UpdateMetadata`; Apple can't edit details, so the same
      "change not made" note would repeat on every run.
- [ ] 401 vs 403 semantics: which means a bad developer token, which a missing or expired
      user token.

**Both**

- [ ] Copy each endpoint's recorded response from `recorded/raw/` into
      `tests/fixtures/<service>/recorded/<fixture name>.json`; the shape tests then use it.
- [ ] Re-run the full check, then `LINERSYNC_LIVE=1 uv run pytest -m live -q`.
- [ ] Delete the live-test playlists. Apple ones must be deleted by hand in the Music app,
      because Apple's API cannot delete playlists.
