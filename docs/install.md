# Installing LinerSync

LinerSync runs as a Docker container, set up with Docker Compose. You need Docker with
Compose on the computer that will run it. The installer, `install.sh`, asks a few
questions and writes the files. It never runs Docker, never needs `sudo` and never uses
the network.

## Running the installer

```sh
curl -fsSLO https://raw.githubusercontent.com/BohdainK/LinerSync/main/install.sh
bash install.sh
```

By default it writes to `./linersync`. Use `bash install.sh --dir /path/to/folder` to
choose another folder. All questions come first; nothing is written until you have
answered the last one. If the input ends partway (Ctrl-D), nothing is changed. A wrong
answer prints a message and asks again. Press Enter to take the answer in brackets.

When it has finished:

```sh
cd linersync && docker compose up -d
```

Open the address the installer printed and sign in.

### The questions

**Already set up?** If the folder already holds a `docker-compose.yml` or `.env`, the
installer asks whether to replace its `docker-compose.yml`, `.env` and `Caddyfile`. The
data folder is kept. Answer `y` to continue; anything else changes nothing.

**How will you open LinerSync?**

1. *Only on this computer.* The default. LinerSync listens on `127.0.0.1` only.
2. *From other devices, with HTTPS set up for me.* Adds a Caddy proxy to the Compose file.
3. *From other devices, through my own reverse proxy.*

**Setup 1, only on this computer.**

- `Port`: the port on this computer, 8000 by default. You open `http://127.0.0.1:<port>`.

**Setup 2, bundled Caddy proxy.**

- `Domain name`: a name like `music.example.com`, without `https://` or a path.
- `Certificate`: choose 1 for a public domain. Caddy gets a Let's Encrypt certificate by
  itself. Ports 80 and 443 must be reachable from the internet, and the domain must
  point to this computer. Choose 2 for a local network only: Caddy makes its own
  certificate authority, and you install its root certificate on your devices.
- Choice 1 asks for an `Email` for certificate notices. It is optional.
- Choice 2 asks for the `HTTPS port` (443 by default). Other ports are added to the
  address you open, for example `https://music.example.com:8443`.

**Setup 3, your own reverse proxy.**

- `Public HTTPS address`: the address people type, such as `https://music.example.com`.
  Your proxy handles HTTPS, so it must start with `https://`. No path.
- `Which proxy do you use?` Caddy, Traefik, Nginx or another. The installer prints the
  configuration to add to your proxy at the end.
- `Does the proxy run on this computer?` Yes: LinerSync listens on `127.0.0.1` only.
  No: LinerSync's port is open to your network without HTTPS, so let only your proxy
  reach it. The snippet then uses `ADDRESS-OF-THIS-COMPUTER`, which you replace.
- `Port`: the port LinerSync listens on, 8000 by default.

**For every setup:**

- `Data folder`: where the database and `secret.key` go, `./data` by default. A relative
  path is relative to the install folder.
- `Timezone`: the timezone schedules are read in. It is detected from your system.
- `Admin password`: leave it empty to generate one. A password you type needs at least 8
  characters and no `'`, and is asked twice. A generated password is printed once at the
  end, so note it.

### Files it writes

In the install folder:

- `docker-compose.yml`: the LinerSync service (and Caddy, for setup 2).
- `.env`: your admin password, readable only by you. Keep it private.
- `Caddyfile`: setup 2 only. It is removed if you re-run the installer with another setup.

The installer also creates the data folder. It never changes an existing data folder.

The container runs as your user and group (`id -u` and `id -g`), so the data folder is
yours. To use another image or user, set `LINERSYNC_IMAGE`, `LINERSYNC_UID` or
`LINERSYNC_GID` when you run the installer.

### Running it again

Run the installer again to change your answers. It asks before it replaces anything and
leaves your data alone. Then run `docker compose up -d` again. If you switch away from
setup 2, the installer prints `docker compose up -d --remove-orphans` instead, so the old
Caddy container stops and frees ports 80 and 443.

## Setting up by hand

Copy the example for your setup from `deploy/examples/` in the repository, and edit it:

- `same-machine/docker-compose.yml`
- `bundled-proxy/docker-compose.yml` and `bundled-proxy/Caddyfile`
- `own-proxy/`: Compose file plus proxy snippets. See [reverse-proxy.md](reverse-proxy.md).

Create a `.env` next to the Compose file with one line,
`LINERSYNC_ADMIN_PASSWORD='choose-a-password'`, and set `LINERSYNC_PUBLIC_URL` to the
address your browser uses. The single quotes keep `$`, `#` and spaces literal. Then run
`docker compose up -d`.

On Windows, use the examples this way too: there is no Windows installer.

## Running without Docker

Run the server and a built web app yourself. See [server.md](server.md): build the
frontend with `npm run build` and start the server with `LINERSYNC_WEB_DIR` pointing at
`frontend/dist`, as described in [The web app](server.md#the-web-app).

## Updating

```sh
docker compose pull && docker compose up -d
```

Restarts are safe. See [what to expect](server.md#what-to-expect).

## Moving to another machine

Stop LinerSync, then copy the whole data folder (`linersync.db` and `secret.key`
together) and the install folder to the new machine, and run `docker compose up -d`
there. If the address changes, update `LINERSYNC_PUBLIC_URL` and register the new
sign-in address with Spotify.

## Uninstalling

```sh
docker compose down
```

Then delete the install folder. That deletes your data too, if the data folder is inside
it. Take a backup first if you might want it.

## Troubleshooting

**Every save fails with 403.** `LINERSYNC_PUBLIC_URL` differs from the address in your
browser. Make them identical, including `https://` and any port, and run
`docker compose up -d` again. See [Access setups](server.md#access-setups).

**Spotify says "INVALID_CLIENT: Invalid redirect URI".** The address registered in the
Spotify dashboard is not the exact sign-in address. Register
`<public URL>/api/connections/oauth/callback`, as the app's setup step shows it. This
happens after moving LinerSync behind a tunnel or reverse proxy: the public URL changed,
so add the new HTTPS redirect URI (for example
`https://music.example.com/api/connections/oauth/callback`) to the Spotify app.

**The container exits with "can't write to the data folder".** The user in the Compose
file's `user:` line cannot write to the data folder. Give that user the folder, for
example `sudo chown -R 1000:1000 ./data` for `user: "1000:1000"`.

**Caddy can't get a certificate.** Check that the domain points to this computer and
that ports 80 and 443 are reachable from the internet. Look at `docker compose logs caddy`.

**The browser warns about the certificate (local certificate authority).** Install
Caddy's root certificate on that device. Copy it out with
`docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt ./caddy-root.crt`
and trust it in the device's certificate settings.
