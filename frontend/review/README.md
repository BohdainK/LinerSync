# UI review build

A single HTML file with the real frontend running on recorded demo data, plus a review drawer for
walking every screen and leaving notes (optionally pinned to an element). Notes export as Markdown.
Nothing you click is saved. Open it from anywhere: no server, no install.

```sh
npm run review:build   # → dist-review/linersync-ui-review.html
```

The build replays `recording.json`. Re-record it after UI or API changes:

```sh
# terminal 1: demo world (fake Spotify and Tidal with real-looking playlists)
cd backend && uv run python -m tests.review_server
# terminal 2: the frontend, proxied to it
cd frontend && LINERSYNC_API=http://127.0.0.1:8766 npx vite --port 5174 --strictPort
# terminal 3: seed, crawl every screen, write review/recording.json
cd frontend && npm run review:record
```

Start the demo server fresh for each recording; the seeding assumes an empty world.

Reads come from the recording (a route recorded with other query parameters still answers).
Writes are acknowledged with a toast and not stored. A view whose data was never recorded says so.
