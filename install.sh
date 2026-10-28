#!/usr/bin/env bash
# LinerSync installer: asks a few questions, then writes docker-compose.yml, .env
# (and a Caddyfile for the bundled proxy) into one folder. It never runs docker,
# sudo or anything on the network. Works with bash 3.2 (macOS) and later.
set -euo pipefail

IMAGE="${LINERSYNC_IMAGE:-ghcr.io/bohdaink/linersync:latest}"
RUN_UID="${LINERSYNC_UID:-$(id -u)}"
RUN_GID="${LINERSYNC_GID:-$(id -g)}"
CALLBACK_PATH="/api/connections/oauth/callback"

DIR="./linersync"
ANSWER=""
MODE=""          # 1 this computer, 2 bundled Caddy proxy, 3 own reverse proxy
PORT=""          # mode 1: port on 127.0.0.1; mode 3: LinerSync's own port
PROXY=""         # mode 3: 1 Caddy, 2 Traefik, 3 Nginx, 4 another
PROXY_LOCAL=""   # mode 3: y when the proxy runs on this computer
PROXY_HOST=""    # mode 3: host of the public address
PROXY_SITE=""    # mode 3: host, plus :port when not 443
PROXY_TLS_PORT="443"
UPSTREAM=""      # mode 3: host:port the proxy forwards to
DOMAIN=""        # mode 2
CERT=""          # mode 2: 1 Let's Encrypt, 2 Caddy's local CA
EMAIL=""         # mode 2 + Let's Encrypt (optional)
HTTPS_PORT="443" # mode 2 + local CA
DATA_VOLUME=""   # data folder as written in docker-compose.yml
DATA_HOST=""     # data folder as a path from the current directory
TIMEZONE=""
PASSWORD=""
GENERATED=""
PUBLIC_URL=""
HAD_CADDYFILE="" # y when DIR/Caddyfile existed before this run

usage() {
	cat <<'EOF'
Usage: install.sh [--dir DIR]

Sets up LinerSync to run with Docker Compose. Asks how you will open it, then
writes docker-compose.yml, .env and (for the bundled proxy) a Caddyfile.

  --dir DIR   Folder to set up in (default: ./linersync)
  --help      Show this help

Environment: LINERSYNC_IMAGE (default ghcr.io/bohdaink/linersync:latest),
LINERSYNC_UID / LINERSYNC_GID (default: your user and group id).
EOF
}

say() {
	printf '%s\n' "$*"
}

input_ended() {
	say "linersync install: no answer (input ended). Nothing was changed." >&2
	exit 1
}

# ask PROMPT [secret] -> ANSWER. Reads one line; end of input stops the installer.
ask() {
	printf '%s ' "$1"
	ANSWER=""
	if [ "${2:-}" = secret ] && [ -t 0 ]; then
		IFS= read -r -s ANSWER || input_ended
		printf '\n'
		return
	fi
	if ! IFS= read -r ANSWER; then
		[ -n "$ANSWER" ] || input_ended
	fi
	# Piped answers aren't echoed; end the prompt line so the output stays readable.
	[ -t 0 ] || printf '\n'
}

# ask_port_number PROMPT DEFAULT -> ANSWER (a port without leading zeros)
ask_port_number() {
	while :; do
		ask "$1"
		ANSWER="${ANSWER:-$2}"
		if [[ "$ANSWER" =~ ^[0-9]{1,5}$ ]] && ((10#$ANSWER >= 1 && 10#$ANSWER <= 65535)); then
			ANSWER=$((10#$ANSWER))
			return
		fi
		say "Enter a port from 1 to 65535."
	done
}

parse_args() {
	while [ $# -gt 0 ]; do
		case "$1" in
		--dir)
			if [ $# -lt 2 ] || [ -z "$2" ]; then
				usage >&2
				exit 2
			fi
			DIR="$2"
			shift 2
			;;
		--help | -h)
			usage
			exit 0
			;;
		*)
			usage >&2
			exit 2
			;;
		esac
	done
}

# --- Questions (all asked before anything is written) ---

confirm_replace() {
	if [ -e "$DIR/docker-compose.yml" ] || [ -e "$DIR/.env" ]; then
		ask "LinerSync is already set up in $DIR. Replace its docker-compose.yml, .env and Caddyfile? The data folder is kept. [y/N]:"
		case "$ANSWER" in
		y | Y) ;;
		*)
			say "Nothing was changed."
			exit 1
			;;
		esac
	fi
}

ask_mode() {
	say "How will you open LinerSync?"
	say "  1) Only on this computer (http://127.0.0.1)"
	say "  2) From other devices, with HTTPS set up for me (bundled Caddy proxy)"
	say "  3) From other devices, through my own reverse proxy"
	while :; do
		ask "Choose 1-3 [1]:"
		case "${ANSWER:-1}" in
		1 | 2 | 3)
			MODE="${ANSWER:-1}"
			return
			;;
		esac
		say "Choose 1, 2 or 3."
	done
}

ask_port() {
	ask_port_number "Port [8000]:" 8000
	PORT="$ANSWER"
}

# Mode 3: sets PUBLIC_URL, PROXY_HOST and PROXY_TLS_PORT from a https:// address.
ask_public_address() {
	local pattern='^https://([A-Za-z0-9.-]+)(:([0-9]{1,5}))?/?$'
	while :; do
		ask "Public HTTPS address (for example https://music.example.com):"
		if [[ "$ANSWER" =~ $pattern ]]; then
			PROXY_HOST="${BASH_REMATCH[1]}"
			PROXY_TLS_PORT="${BASH_REMATCH[3]:-443}"
			if ((10#$PROXY_TLS_PORT >= 1 && 10#$PROXY_TLS_PORT <= 65535)); then
				PROXY_TLS_PORT=$((10#$PROXY_TLS_PORT))
				PUBLIC_URL="${ANSWER%/}"
				return
			fi
		fi
		case "$ANSWER" in
		http://*) say "Use the https:// address; your proxy handles HTTPS." ;;
		*) say "Enter an address like https://music.example.com, without a path." ;;
		esac
	done
}

ask_proxy() {
	say "Which proxy do you use?"
	say "  1) Caddy"
	say "  2) Traefik"
	say "  3) Nginx"
	say "  4) Another proxy"
	while :; do
		ask "Choose 1-4 [1]:"
		case "${ANSWER:-1}" in
		1 | 2 | 3 | 4)
			PROXY="${ANSWER:-1}"
			return
			;;
		esac
		say "Choose 1, 2, 3 or 4."
	done
}

ask_proxy_local() {
	while :; do
		ask "Does the proxy run on this computer? [Y/n]:"
		case "$ANSWER" in
		"" | y | Y)
			PROXY_LOCAL=y
			return
			;;
		n | N)
			PROXY_LOCAL=n
			return
			;;
		esac
		say "Answer y or n."
	done
}

ask_own_proxy() {
	ask_public_address
	ask_proxy
	ask_proxy_local
	ask_port
}

ask_domain() {
	local label='[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?'
	local pattern="^${label}(\\.${label})*\$"
	while :; do
		ask "Domain name (for example music.example.com):"
		if [[ "$ANSWER" =~ $pattern ]]; then
			DOMAIN="$ANSWER"
			return
		fi
		say "Enter a name like music.example.com, without https:// or a path."
	done
}

ask_certificate() {
	say "Certificate:"
	say "  1) Public domain: automatic Let's Encrypt certificate (ports 80 and 443 must be reachable from the internet)"
	say "  2) Local network only: Caddy's own certificate authority (you install its root certificate on your devices)"
	while :; do
		ask "Choose 1-2 [1]:"
		case "${ANSWER:-1}" in
		1 | 2)
			CERT="${ANSWER:-1}"
			return
			;;
		esac
		say "Choose 1 or 2."
	done
}

ask_email() {
	local pattern='^[^[:space:]{}"@]+@[^[:space:]{}"@]+$'
	while :; do
		ask "Email for certificate notices (optional):"
		if [ -z "$ANSWER" ] || [[ "$ANSWER" =~ $pattern ]]; then
			EMAIL="$ANSWER"
			return
		fi
		say "Enter an email address, or leave it empty."
	done
}

ask_https_port() {
	ask_port_number "HTTPS port [443]:" 443
	HTTPS_PORT="$ANSWER"
}

# True for ".", "./", "./." and the like: the setup folder itself, which would
# mount docker-compose.yml and .env into the container's data folder.
is_setup_folder() {
	local rest="$1"
	while :; do
		case "$rest" in
		./*) rest="${rest#./}" ;;
		*/.) rest="${rest%/.}" ;;
		*/) rest="${rest%/}" ;;
		*) break ;;
		esac
	done
	[ -z "$rest" ] || [ "$rest" = . ]
}

ask_data_dir() {
	local folder
	while :; do
		ask "Data folder [./data]:"
		folder="${ANSWER:-./data}"
		case "$folder" in
		*'"'* | *'\'* | *'$'* | *:*)
			say "Choose another folder name."
			continue
			;;
		esac
		if is_setup_folder "$folder"; then
			say "Choose another folder name."
			continue
		fi
		case "$folder" in
		"~") folder="$HOME" ;;
		"~/"*) folder="$HOME/${folder#\~/}" ;;
		esac
		case "$folder" in
		/*)
			DATA_VOLUME="$folder"
			DATA_HOST="$folder"
			;;
		./*)
			DATA_VOLUME="$folder"
			DATA_HOST="$DIR/${folder#./}"
			;;
		../*)
			DATA_VOLUME="$folder"
			DATA_HOST="$DIR/$folder"
			;;
		*)
			DATA_VOLUME="./$folder"
			DATA_HOST="$DIR/$folder"
			;;
		esac
		return
	done
}

detect_timezone() {
	local tz="${TZ:-}" link=""
	tz="${tz#:}"
	if [ -z "$tz" ] && [ -r /etc/timezone ]; then
		IFS= read -r tz </etc/timezone || true
	fi
	if [ -z "$tz" ] && [ -L /etc/localtime ]; then
		link="$(readlink /etc/localtime || true)"
		case "$link" in
		*zoneinfo/*) tz="${link##*zoneinfo/}" ;;
		esac
	fi
	printf '%s' "${tz:-UTC}"
}

valid_timezone() {
	local pattern='^[A-Za-z0-9_+-]+(/[A-Za-z0-9_+-]+)*$'
	[[ "$1" =~ $pattern ]] || return 1
	if [ -d /usr/share/zoneinfo ]; then
		[ -f "/usr/share/zoneinfo/$1" ] || return 1
	fi
	return 0
}

ask_timezone() {
	local detected
	detected="$(detect_timezone)"
	while :; do
		ask "Timezone [$detected]:"
		ANSWER="${ANSWER:-$detected}"
		if valid_timezone "$ANSWER"; then
			TIMEZONE="$ANSWER"
			return
		fi
		say "Unknown timezone. Use a name like Europe/Amsterdam."
	done
}

generate_password() {
	local pw="" tries=0
	# head stops first and the rest of the pipe drains its output, so nothing
	# gets SIGPIPE under pipefail. A failing tool leaves pw short: give up
	# after a few tries instead of looping forever.
	while [ "$tries" -lt 5 ]; do
		tries=$((tries + 1))
		pw="$(head -c 64 /dev/urandom | base64 | LC_ALL=C tr -dc 'A-Za-z0-9' | cut -c1-24)" || pw=""
		if [ "${#pw}" -eq 24 ]; then
			printf '%s' "$pw"
			return
		fi
	done
	say "linersync install: couldn't generate a password (head, base64 or tr failed on /dev/urandom). Nothing was changed." >&2
	exit 1
}

ask_password() {
	local first
	while :; do
		ask "Admin password (leave empty to generate one):" secret
		first="$ANSWER"
		if [ -z "$first" ]; then
			PASSWORD="$(generate_password)"
			GENERATED=1
			return
		fi
		if [ "${#first}" -lt 8 ]; then
			say "Use at least 8 characters."
			continue
		fi
		case "$first" in
		*"'"*)
			say "The password can't contain '."
			continue
			;;
		esac
		ask "Repeat the password:" secret
		if [ "$ANSWER" != "$first" ]; then
			say "The passwords don't match."
			continue
		fi
		PASSWORD="$first"
		return
	done
}

# --- Output ---

set_public_url() {
	case "$MODE" in
	1) PUBLIC_URL="http://127.0.0.1:$PORT" ;;
	2)
		PUBLIC_URL="https://$DOMAIN"
		if [ "$CERT" = 2 ] && [ "$HTTPS_PORT" != 443 ]; then
			PUBLIC_URL="$PUBLIC_URL:$HTTPS_PORT"
		fi
		;;
	3)
		# PUBLIC_URL was already set by ask_public_address.
		PROXY_SITE="$PROXY_HOST"
		if [ "$PROXY_TLS_PORT" != 443 ]; then
			PROXY_SITE="$PROXY_HOST:$PROXY_TLS_PORT"
		fi
		if [ "$PROXY_LOCAL" = y ]; then
			UPSTREAM="127.0.0.1:$PORT"
		else
			UPSTREAM="ADDRESS-OF-THIS-COMPUTER:$PORT"
		fi
		;;
	esac
}

# The port mapping for the linersync service, or empty for none.
linersync_ports() {
	case "$MODE" in
	1) printf '%s' "127.0.0.1:$PORT:8000" ;;
	3)
		if [ "$PROXY_LOCAL" = y ]; then
			printf '%s' "127.0.0.1:$PORT:8000"
		else
			printf '%s' "$PORT:8000"
		fi
		;;
	esac
}

compose_caddy() {
	say ""
	say "  caddy:"
	say "    image: caddy:2"
	say "    restart: unless-stopped"
	say "    depends_on:"
	say "      - linersync"
	say "    ports:"
	if [ "$CERT" = 1 ]; then
		say '      - "80:80"'
		say '      - "443:443"'
		say '      - "443:443/udp"'
	else
		say "      - \"$HTTPS_PORT:$HTTPS_PORT\""
		say "      - \"$HTTPS_PORT:$HTTPS_PORT/udp\""
	fi
	say "    volumes:"
	say "      - ./Caddyfile:/etc/caddy/Caddyfile:ro"
	say "      - caddy_data:/data"
	say "      - caddy_config:/config"
	say ""
	say "volumes:"
	say "  caddy_data:"
	say "  caddy_config:"
}

write_compose() {
	local ports
	ports="$(linersync_ports)"
	{
		say "# Written by LinerSync's install.sh. Start it with: docker compose up -d"
		say "services:"
		say "  linersync:"
		say "    image: $IMAGE"
		say "    restart: unless-stopped"
		say "    user: \"$RUN_UID:$RUN_GID\""
		if [ -n "$ports" ]; then
			say "    ports:"
			say "      - \"$ports\""
		fi
		say "    env_file: .env"
		say "    environment:"
		say "      LINERSYNC_PUBLIC_URL: \"$PUBLIC_URL\""
		say "      LINERSYNC_TIMEZONE: \"$TIMEZONE\""
		say "    volumes:"
		say "      - \"$DATA_VOLUME:/data\""
		if [ "$MODE" = 2 ]; then
			compose_caddy
		fi
	} >"$DIR/docker-compose.yml"
}

write_caddyfile() {
	local file="$DIR/Caddyfile"
	if [ "$MODE" != 2 ]; then
		rm -f "$file"
		return
	fi
	{
		if [ "$CERT" = 1 ]; then
			if [ -n "$EMAIL" ]; then
				printf '{\n\temail %s\n}\n\n' "$EMAIL"
			fi
			printf '%s {\n\treverse_proxy linersync:8000\n}\n' "$DOMAIN"
		else
			local site="$DOMAIN"
			if [ "$HTTPS_PORT" != 443 ]; then
				site="$DOMAIN:$HTTPS_PORT"
			fi
			printf '%s {\n\ttls internal\n\treverse_proxy linersync:8000\n}\n' "$site"
		fi
	} >"$file"
}

write_env() {
	local file="$DIR/.env"
	# Remove first so the new file is created with umask 077 (mode 600).
	rm -f "$file"
	(
		umask 077
		printf "LINERSYNC_ADMIN_PASSWORD='%s'\n" "$PASSWORD" >"$file"
	)
}

# The snippets for the user's own proxy. They match deploy/examples/own-proxy/.
snippet_caddy() {
	printf '%s {\n\treverse_proxy %s\n}\n' "$PROXY_SITE" "$UPSTREAM"
}

snippet_traefik() {
	cat <<EOF
# Traefik dynamic configuration (file provider).
http:
  routers:
    linersync:
      rule: "Host(\`$PROXY_HOST\`)"
      entryPoints:
        - websecure
      service: linersync
      tls:
        certResolver: letsencrypt # use the resolver name from your static configuration
  services:
    linersync:
      loadBalancer:
        servers:
          - url: "http://$UPSTREAM"
EOF
}

snippet_nginx() {
	cat <<EOF
server {
	listen $PROXY_TLS_PORT ssl;
	server_name $PROXY_HOST;

	# Use your own certificate files (for example from certbot).
	ssl_certificate /etc/letsencrypt/live/$PROXY_HOST/fullchain.pem;
	ssl_certificate_key /etc/letsencrypt/live/$PROXY_HOST/privkey.pem;

	# Allow JSON imports up to 256 MB (the default limit is 1 MB).
	client_max_body_size 256m;

	location / {
		proxy_pass http://$UPSTREAM;
		proxy_http_version 1.1;
		proxy_set_header Host \$host;
		# Live updates (server-sent events) must not be buffered.
		proxy_buffering off;
		proxy_read_timeout 1h;
	}
}
EOF
}

print_proxy_docker_note() {
	say ""
	say "If your proxy runs in a Docker container, 127.0.0.1 is the container itself and you will get a 502."
	say "Put LinerSync on the proxy's Docker network, remove the ports lines from docker-compose.yml,"
	say "and use linersync:8000 as the upstream. Or answer n to the proxy question and use this computer's address."
}

print_own_proxy() {
	if [ "$PROXY_LOCAL" = n ]; then
		say "LinerSync's port $PORT is open to your network without HTTPS. Let only your proxy reach it."
	fi
	say ""
	case "$PROXY" in
	1)
		say "Add this to your Caddy configuration:"
		snippet_caddy
		;;
	2)
		say "Add this to your Traefik configuration:"
		snippet_traefik
		;;
	3)
		say "Add this to your Nginx configuration:"
		snippet_nginx
		;;
	4)
		say "Set up your proxy like this:"
		say "  - Forward everything to http://$UPSTREAM"
		say "  - Pass the Host, Origin and Content-Type headers through unchanged."
		say "  - Turn response buffering off for /api/events (server-sent events)."
		say "  - Allow request bodies up to 256 MB (JSON import)."
		say "See docs/reverse-proxy.md."
		;;
	esac
	if [ "$PROXY_LOCAL" = y ]; then
		print_proxy_docker_note
	fi
}

print_mode_notes() {
	case "$MODE" in
	3) print_own_proxy ;;
	2)
		if [ "$CERT" = 1 ]; then
			say "Before you start it: $DOMAIN must point to this computer, and ports 80 and 443 must be reachable from the internet."
		else
			say "Copy Caddy's root certificate after the first start with: docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt ./caddy-root.crt"
			say "Every device that opens LinerSync must trust that certificate and resolve $DOMAIN to this computer."
		fi
		;;
	esac
}

print_summary() {
	say ""
	say "Wrote:"
	say "  $DIR/docker-compose.yml"
	if [ "$MODE" = 2 ]; then
		say "  $DIR/Caddyfile"
	fi
	say "  $DIR/.env"
	say "Data folder: $DATA_HOST"
	say ""
	if [ "$HAD_CADDYFILE" = y ] && [ "$MODE" != 2 ]; then
		say "Start it:  cd $(printf '%q' "$DIR") && docker compose up -d --remove-orphans"
	else
		say "Start it:  cd $(printf '%q' "$DIR") && docker compose up -d"
	fi
	say "Open:  $PUBLIC_URL"
	say "Sign-in address to register with Spotify:  $PUBLIC_URL$CALLBACK_PATH"
	if [ -n "$GENERATED" ]; then
		say "Your admin password (shown once): $PASSWORD"
	fi
	print_mode_notes
}

main() {
	parse_args "$@"
	if [ "$RUN_UID" = 0 ]; then
		say "Warning: the container will run as root (user id 0). Run the installer without sudo, or set LINERSYNC_UID and LINERSYNC_GID." >&2
	fi
	if [ -e "$DIR/Caddyfile" ]; then
		HAD_CADDYFILE=y
	fi
	confirm_replace
	ask_mode
	case "$MODE" in
	1) ask_port ;;
	2)
		ask_domain
		ask_certificate
		if [ "$CERT" = 1 ]; then
			ask_email
		else
			ask_https_port
		fi
		;;
	3) ask_own_proxy ;;
	esac
	ask_data_dir
	ask_timezone
	ask_password

	set_public_url
	mkdir -p "$DIR" "$DATA_HOST"
	write_compose
	write_caddyfile
	write_env
	print_summary
}

main "$@"
