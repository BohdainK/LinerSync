# Running the LinerSync server

The server is a single process. It serves the HTTP API, runs scheduled syncs and
works through a queue of jobs (syncs, previews, transfers, undos) one at a time. All
state lives in one data folder.

## Starting it

From `backend/`:

```sh
LINERSYNC_ADMIN_PASSWORD='choose-a-password' uv run linersync serve
```

`serve` is the only command. It reads its settings from the environment (below) and
listens on `127.0.0.1:8000` unless told otherwise. Stop it with Ctrl-C.

The server **refuses to start without `LINERSYNC_ADMIN_PASSWORD`**: it prints
`linersync: LINERSYNC_ADMIN_PASSWORD is required` and exits with status 2. An invalid
`LINERSYNC_PUBLIC_URL`, `LINERSYNC_TIMEZONE` or `LINERSYNC_PORT` also exits with status 2
and a message naming the variable.

## Settings

| Variable | Default | What it does |
|---|---|---|
| `LINERSYNC_ADMIN_PASSWORD` | none (required) | The password for the web UI and the API. |
| `LINERSYNC_DATA_DIR` | `./data` | Folder for the database (`linersync.db`) and `secret.key`. Created if missing. |
| `LINERSYNC_PUBLIC_URL` | `http://127.0.0.1:8000` | The address your browser uses to reach the app. Scheme and host (and port) only, no path. See [Access setups](#access-setups). |
| `LINERSYNC_SECRET_KEY` | unset: read from, or created at, `<data dir>/secret.key` | The key that encrypts service credentials. See [The secret key](#the-secret-key). |
| `LINERSYNC_TIMEZONE` | `UTC` | The timezone schedules are read in, for example `Europe/Amsterdam`. |
| `LINERSYNC_HOST` | `127.0.0.1` | The address to listen on. Use `0.0.0.0` inside a container or behind a proxy on another machine. |
| `LINERSYNC_PORT` | `8000` | The port to listen on. |
| `LINERSYNC_WEB_DIR` | unset | Folder with the built web app (`frontend/dist`). Unset: the server serves only the API. The Docker image sets it. |
| `LINERSYNC_YTMUSIC_AUTH` | `headers` | How YouTube Music signs in. `headers`: the Cookie header of a signed-in music.youtube.com tab, pasted in the setup steps. `oauth`: your own Google Cloud OAuth client ("TVs and Limited Input devices"), signed in with a code. Both are unofficial: YouTube Music has no public API. After switching, set YouTube Music up again. |
| `LINERSYNC_APPLE_MUSIC_AUTH` | `web` | How Apple Music signs in. `web`: the token Apple's web player uses plus your `media-user-token` cookie. Unofficial, and it can break when Apple changes music.apple.com, but needs no developer membership. `developer`: your own MusicKit key and MusicKit sign-in. After switching, set Apple Music up again: the two modes' user tokens are not interchangeable. |

Syncs without their own schedule use the default schedule `0 */6 * * *` (every six
hours). You can change it in the app's settings.

## Access setups

Spotify only accepts sign-in redirects to an HTTPS address or to the loopback address
`127.0.0.1` (not `localhost`), and Apple Music sign-in needs a secure browser context. So
a plain `http://192.168.x.x` address cannot sign in to either service. LinerSync supports
three setups:

1. **Same machine.** Open the app at `http://127.0.0.1:8000` (or your port) on the
   computer that runs it. The defaults already fit this setup.
2. **Bundled reverse proxy.** A Caddy proxy in front of LinerSync handles HTTPS with
   automatic certificates. Set `LINERSYNC_PUBLIC_URL=https://music.example.com` (your
   domain) and let LinerSync listen where the proxy can reach it.
3. **Your own reverse proxy** (Caddy, Traefik, Nginx, ...). Terminate HTTPS at the proxy,
   forward to LinerSync, and set `LINERSYNC_PUBLIC_URL` to the HTTPS address users type.

Tidal sign-in works the same way as Spotify's: register the redirect URI
`<public URL>/api/connections/oauth/callback` in your Tidal developer app. LinerSync
accepts an HTTPS address or a loopback address (`127.0.0.1` or `localhost`) for it.

Jellyfin needs none of this: LinerSync calls your Jellyfin server, never the other way
round, so Jellyfin needs no public URL. Enter an address the LinerSync container can
reach, such as `http://jellyfin:8096` when both run on the same Docker network, or the
server's LAN address.

[install.md](install.md) covers the installer that sets up any of the three, and
[reverse-proxy.md](reverse-proxy.md) covers using your own proxy.

**Why `LINERSYNC_PUBLIC_URL` must match how the browser reaches the app:**

- Service sign-in redirects back to `<public URL>/api/connections/oauth/callback`. The
  setup steps show this exact address, and it is what you register with Spotify. If it
  is not the address the browser can reach, sign-in fails.
- The session cookie is marked `Secure` only when the public URL starts with `https://`.
- Requests that change something are refused (403) when their `Origin` header differs
  from the public URL's origin. A mismatch here makes every save fail.

### Notes for API clients and reverse proxies

Every API request that changes something (`POST`, `PUT`, `PATCH`, `DELETE`) must:

- send `Content-Type: application/json`, **even when it has no body**, such as
  `POST /api/auth/logout` or a `DELETE`. Send `{}` as the body. Without it the server
  answers 415.
- either send no `Origin` header or send one equal to the public URL's origin (for
  example `https://music.example.com`). A different origin gets 403.

A reverse proxy must therefore pass the `Origin` and `Content-Type` headers through
unchanged. Server-sent
events at `/api/events` need response buffering off (LinerSync sends
`X-Accel-Buffering: no` for Nginx).

## Signing in

There is one password, `LINERSYNC_ADMIN_PASSWORD`. Signing in creates a session that
lasts 30 days, kept in an `HttpOnly`, `SameSite=Lax` cookie named `linersync_session`.
After 5 wrong passwords within 15 minutes, sign-in is refused (429) until the window
passes. Every API route except `/api/health` and `/api/auth/login` needs a session and
answers 401 without one.

## The secret key

Service credentials (API keys, tokens) are encrypted in the database with a key. The key
comes from `LINERSYNC_SECRET_KEY` (a Fernet key) or, if that is unset, from
`<data dir>/secret.key`, which the server creates on first start, readable only by its
owner.

What it protects: a **copy of the database on its own** (for example a backup that leaks)
does not reveal your credentials. What it does not protect against: anyone who can read
the whole data folder, because the key file sits next to the database. Protect the data
folder itself.

**Back up `secret.key` together with `linersync.db`.** If you restore the database
without its key, or the key changes, LinerSync still starts and loses nothing else, but
every service shows as "not set up" and you have to enter its credentials again.

## API documentation

The API is described with OpenAPI. Interactive docs are at `/api/docs`, and the schema at
`/api/openapi.json`. `/api/health` answers without a session and reports the version.

## The web app

The web app in `frontend/` talks to this API. To serve a build from the server, run
`npm run build` in `frontend/`, then from `backend/`:

```sh
LINERSYNC_WEB_DIR=../frontend/dist uv run linersync serve
```

The server then answers every non-API address with the app, so links such as
`/syncs/abc` work after a refresh. With `LINERSYNC_WEB_DIR` set but no `index.html` in
it, the server exits with status 2. For development, start the server with
`LINERSYNC_PUBLIC_URL=http://127.0.0.1:5173` and run the Vite dev server.
See [frontend.md](frontend.md).

## What to expect

**One thing at a time.** Jobs run one after another in the order they were queued. A
scheduled sync that comes due while the same sync is already queued or running does not
queue a second copy.

**Cancelling** stops a job only before it starts changing your playlists. Once it has
written its first change, pressing cancel is noted but the run finishes, including its
check that the playlist ended up as planned. So a cancelled run never leaves a playlist
half-changed and unchecked. This may take a few seconds more.

**Restarting the server** (an update, a crash, a container restart) is safe:

- A job that was running shows as *interrupted*. A sync run that was in the middle of
  writing shows as *aborted* ("LinerSync stopped during this run") and can still be
  undone.
- Jobs that were waiting in the queue run after the restart.
- Nothing re-runs by itself. The sync's next scheduled run picks up where things are,
  and a sync always compares against the playlists as they are now, so that run is safe.

Stopping the server with Ctrl-C (or the container's stop signal) behaves the same way.

**Editing a sync while it runs.** A sync can't be edited or deleted while a job for it is
queued or running; the app answers 409 "Wait for the current run to finish". Wait for the job to finish, or cancel
it, and try again. This protects your edit: the run would otherwise overwrite it when it
finishes.

## Backups, archive and export

**What a backup holds.** For each playlist: its name and description, its songs in
order, and its cover art. LinerSync keeps up to *N* versions of each playlist
(`backup_versions_kept` in the settings, 5 by default, 1 to 100), newest first. A new
version is saved only when the playlist's content changed. A backup runs every day at
03:00 (in `LINERSYNC_TIMEZONE`) by default. Change this with `backup_schedule` in the
settings; `null` turns scheduled backups off. You can also back up at any time from the
Backup page. A backup only reads from your services and can be cancelled at any time.
A service that fails does not stop the others, and a playlist that could not be read in
full never replaces what was saved.

**The archive.** When a backup finds that a playlist is gone from its service, the
playlist is archived: its versions stay and it is listed as archived.

- A playlist that is the destination of a running sync is left to the sync, which
  recreates it. It is only marked as missing and checked again on every backup.
- A playlist that comes back leaves the archive on the next backup, with its history.
- Archived playlists stay until you delete them. Deleting is refused while a backup is
  queued or running (409 "Wait for the current backup to finish").

**Restoring** always creates a new playlist and never overwrites one. It needs the
service connected: otherwise the app says "Connect X first". By default the playlist
goes back to the service it came from. An archived playlist can also be the source of
a transfer or a sync.

**Disconnecting** a service keeps its backups. To remove them too, choose "also delete
this service's backups" (`DELETE /api/connections/{service}?delete_backups=true`). Sync
run history is not touched.

**CSV and JSON export.** The CSV export gives one file per playlist (its latest
version), opening correctly in spreadsheets. Several playlists download as one zip with
archived ones under `archive/`. The JSON export (`GET /api/export`) holds your settings,
syncs, run history, remembered matches, backups (latest version of each playlist) and
cover art. It leaves out credentials and other secrets (including the secret key),
sessions, jobs, attention items and undo snapshots, so imported runs can't be undone.
An export holds your library data unencrypted: store it like the data folder.

**Restoring a JSON export** (`POST /api/import`) merges it into this install, and your
local data wins: anything that already exists is skipped. Imported syncs are paused, so
nothing runs until you check and resume them. Your current settings are kept unless they
were never changed. Importing the same file twice adds nothing the second time. For API
clients:

- `413`: the file is larger than 256 MB.
- `409`: a backup is queued or running; try again when it has finished.
- `422`: the file isn't a LinerSync export, was made by a newer version, or is damaged.
  Nothing is written in that case.
