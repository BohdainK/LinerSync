# LinerSync

LinerSync is a free, self-hosted app that keeps your music playlists in sync across
streaming services, and keeps a central backup and archive of your library. It runs as
one Docker container on your own computer or server. Your credentials and your library
data stay with you.

Before you start, know what each service asks of you:

- **Spotify:** a Spotify Premium account owns the developer app you register.
- **Apple Music:** needs a MusicKit key, only available with paid Apple Developer Program
  membership OR a token from the apple music web player
- **Tidal:** a Tidal subscription, and a free app you register on the Tidal developer
  dashboard.
- **YouTube Music:** a signed-in browser to copy a cookie from, or (with
  `LINERSYNC_YTMUSIC_AUTH=oauth`) a free Google Cloud OAuth client. YouTube Music has no
  public API, so this connection can break when YouTube changes its site.
- **Qobuz (experimental):** a Qobuz subscription and a signed-in web player to copy two
  values from. Qobuz has no API for apps like LinerSync, so this uses the unofficial one
  behind its web player. It can break when Qobuz updates the player.
- **Jellyfin:** an API key from your server's dashboard (read-only: Jellyfin can be a
  source, not a destination).

## What it does

- Connects Spotify, Apple Music, Tidal, YouTube Music and (experimental) Qobuz, and
  reads playlists from Jellyfin, with step-by-step setup in the app.
- Syncs playlists one way, from a source to one or more destinations, in full or
  add-only mode. Preview and dry run show what would change first.
- Transfers playlists between services once.
- Matches songs across services, and asks you when it is not sure ("Needs attention").
  Your answers are remembered.
- Backs up every playlist with versions, archives playlists that disappear from a
  service, and exports CSV and JSON. A JSON export can be restored.
- Shows stats: how many playlists and songs are backed up per service, what the last
  7, 30 or 90 days of syncs, transfers and backups did, and how much matching has
  learned.

## Safety

- A failed or incomplete read is never treated as "empty". The run stops instead.
- A run that would remove a large part of a playlist pauses and waits for your approval.
- The playlist is saved before every change, and any run can be undone from its history.
- After a change, LinerSync reads the playlist again and flags any difference from the
  plan. Removals happen last, after additions succeed.
- A service that keeps failing is paused instead of retried forever, and retries never
  add a song twice.

## Quick start

You need Docker with Compose.

```sh
curl -fsSLO https://raw.githubusercontent.com/BohdainK/LinerSync/main/install.sh
bash install.sh
cd linersync && docker compose up -d
```

The installer asks a few questions, writes the files in a `linersync` folder and does
not start anything. Open the address it prints, sign in with the admin password, and
follow the setup steps in the app. See [docs/install.md](docs/install.md) for every
question.

## Three ways to reach it

Spotify only accepts sign-in on HTTPS or on `127.0.0.1`, so how you open LinerSync
matters.

1. **Only on this computer** at `http://127.0.0.1:8000`. The simplest setup.
2. **From other devices, with a bundled Caddy proxy** that sets up HTTPS for a domain
   name you own. See [docs/install.md](docs/install.md).
3. **From other devices, through your own reverse proxy or tunnel** (Caddy, Traefik,
   Nginx, a Cloudflare Tunnel or another). See [docs/reverse-proxy.md](docs/reverse-proxy.md).

## Connecting your services

**Spotify.** In the Spotify developer dashboard, create an app. Add the redirect URI
`<public URL>/api/connections/oauth/callback`, written exactly as the setup steps in the
app show it. For the same-machine setup that is
`http://127.0.0.1:8000/api/connections/oauth/callback`. Behind your own tunnel or reverse
proxy it is the HTTPS address you open LinerSync at, for example
`https://music.example.com/api/connections/oauth/callback` when `LINERSYNC_PUBLIC_URL` is
`https://music.example.com`. Spotify accepts several redirect URIs, so after moving
LinerSync to a new address, add the new one. Then enter the app's client ID in LinerSync.

**Apple Music.** Follow the setup steps in the app. They need the MusicKit key from your
Apple Developer Program membership, or (the default) a token from the Apple Music web
player. Apple Music is add only: Apple's API can't remove or reorder songs or delete
playlists. **Experimental:** with the web player token, turning on Apple Music playlist
editing under Settings, Experimental lets LinerSync do all three (or set
`LINERSYNC_APPLE_MUSIC_EDITING=on` on the server). That only works by borrowing the web
player's token, not with Apple's official API, and it can break whenever Apple changes
its web player. See [docs/server.md](docs/server.md#experimental-features).

**Tidal.** On the Tidal developer dashboard, create an app with the scopes the setup
steps list, and add the same redirect URI as for Spotify. Then enter the app's client ID
in LinerSync and sign in.

**YouTube Music.** Follow the setup steps in the app: paste the Cookie header from a
signed-in music.youtube.com tab, or, in OAuth mode, enter your Google OAuth client and
the code Google shows.

**Qobuz.** Follow the setup steps in the app: copy the `X-App-Id` and
`X-User-Auth-Token` request headers from a signed-in play.qobuz.com tab. Qobuz can't
reorder songs through LinerSync.

**Jellyfin.** In Jellyfin, open Dashboard → API Keys and create a key. In LinerSync enter
your server's address, the key and the username whose playlists to read.

## Backups

Your data lives in the data folder (`./data` next to the Compose file by default). Back
up the whole folder. The two files that matter are `linersync.db` and `secret.key`, and
they belong together: without the key, every service shows as "not set up" and you enter
its credentials again. The folder holds your library data and the key to your
credentials, so protect it. The JSON export in the app is a second way to keep your
settings and backups; see [docs/server.md](docs/server.md#backups-archive-and-export).

## Updating

```sh
docker compose pull && docker compose up -d
```

Restarting is safe: a run that was in progress shows as interrupted or aborted, and
nothing re-runs by itself. See [what to expect](docs/server.md#what-to-expect).

## Documentation

- [Running the server](docs/server.md): settings, sign-in, the secret key, backups.
- [Installing](docs/install.md): the installer, setting up by hand, troubleshooting.
- [Your own reverse proxy](docs/reverse-proxy.md)
- [Frontend development](docs/frontend.md)
- [Connector guide](docs/connectors.md): adding a music service.

## License

LinerSync is licensed under PolyForm Noncommercial 1.0.0
,