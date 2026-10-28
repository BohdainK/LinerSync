# Using your own reverse proxy

LinerSync needs HTTPS to sign in to Spotify and Apple Music from any address other than
`127.0.0.1`. See [Access setups](server.md#access-setups) for why. If you already run a
reverse proxy (Caddy, Traefik, Nginx or another), let it handle HTTPS and forward to
LinerSync. The installer (`install.sh`, choice 3) writes the Compose file and prints the
snippet for your proxy. This page shows the same files for doing it by hand.

## What any proxy must do

- Forward everything to LinerSync, for example `http://127.0.0.1:8000`.
- Pass the `Host`, `Origin` and `Content-Type` headers through unchanged. Requests that
  change something are refused when `Origin` differs from the public address.
- Turn response buffering off for `/api/events` (server-sent events), so live updates
  arrive at once. Turning it off for everything is fine.
- Allow request bodies up to 256 MB, because a JSON import can be large. Nginx's default
  limit is 1 MB.

## LinerSync's Compose file

This file is for a proxy on the same computer: LinerSync listens on `127.0.0.1:8000`
only, so nothing but your proxy can reach it. Put your admin password in `.env` next to
it as `LINERSYNC_ADMIN_PASSWORD='...'`.

```yaml
# Written by LinerSync's install.sh. Start it with: docker compose up -d
services:
  linersync:
    image: ghcr.io/bohdaink/linersync:latest
    restart: unless-stopped
    user: "1000:1000"
    ports:
      - "127.0.0.1:8000:8000"
    env_file: .env
    environment:
      LINERSYNC_PUBLIC_URL: "https://music.example.com"
      LINERSYNC_TIMEZONE: "Europe/Amsterdam"
    volumes:
      - "./data:/data"
```

If your proxy runs on another computer, change the port line to `"8000:8000"` and let
only that computer reach port 8000 (for example with a firewall rule). That port has no
HTTPS. In the proxy examples below, replace `127.0.0.1` with the address of the computer
that runs LinerSync.

If your proxy runs in a Docker container, `127.0.0.1` is the container itself and you
get a 502. Put LinerSync on the proxy's Docker network, remove the `ports` lines from
the Compose file, and use `linersync:8000` as the upstream in the examples below. Or tell
the installer the proxy runs elsewhere and use this computer's address.

## Caddy

Add this to your Caddyfile. Caddy gets the certificate and streams responses without
extra settings.

```caddyfile
music.example.com {
	reverse_proxy 127.0.0.1:8000
}
```

## Traefik

Add this as a dynamic configuration file (file provider). Use the certificate resolver
name from your static configuration in place of `letsencrypt`.

```yaml
# Traefik dynamic configuration (file provider).
http:
  routers:
    linersync:
      rule: "Host(`music.example.com`)"
      entryPoints:
        - websecure
      service: linersync
      tls:
        certResolver: letsencrypt # use the resolver name from your static configuration
  services:
    linersync:
      loadBalancer:
        servers:
          - url: "http://127.0.0.1:8000"
```

If Traefik runs in Docker, you can use container labels instead of a file. Put LinerSync
on Traefik's network and remove `ports` from the Compose file:

```yaml
services:
  linersync:
    image: ghcr.io/bohdaink/linersync:latest
    restart: unless-stopped
    user: "1000:1000"
    env_file: .env
    environment:
      LINERSYNC_PUBLIC_URL: "https://music.example.com"
      LINERSYNC_TIMEZONE: "Europe/Amsterdam"
    volumes:
      - "./data:/data"
    networks:
      - traefik
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.linersync.rule=Host(`music.example.com`)"
      - "traefik.http.routers.linersync.entrypoints=websecure"
      - "traefik.http.routers.linersync.tls.certresolver=letsencrypt"
      - "traefik.http.services.linersync.loadbalancer.server.port=8000"

networks:
  traefik:
    external: true
```

Replace `traefik` with the name of Traefik's Docker network and `letsencrypt` with your
resolver name.

## Nginx

Add this server block (for example in `/etc/nginx/conf.d/linersync.conf`) and point the
certificate lines at your own files. The block raises the body limit and turns off
buffering, which Nginx's defaults would otherwise get in the way of imports and live
updates. Do not set `proxy_set_header Origin`.

```nginx
server {
	listen 443 ssl;
	server_name music.example.com;

	# Use your own certificate files (for example from certbot).
	ssl_certificate /etc/letsencrypt/live/music.example.com/fullchain.pem;
	ssl_certificate_key /etc/letsencrypt/live/music.example.com/privkey.pem;

	# Allow JSON imports up to 256 MB (the default limit is 1 MB).
	client_max_body_size 256m;

	location / {
		proxy_pass http://127.0.0.1:8000;
		proxy_http_version 1.1;
		proxy_set_header Host $host;
		# Live updates (server-sent events) must not be buffered.
		proxy_buffering off;
		proxy_read_timeout 1h;
	}
}
```

## Another proxy

Follow the list under "What any proxy must do".

## Finish

Then set `LINERSYNC_PUBLIC_URL` to the HTTPS address and register
`<public URL>/api/connections/oauth/callback` with Spotify.
