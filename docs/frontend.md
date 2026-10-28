# The LinerSync web app

The web app lives in `frontend/`. It is a Vite + React + TypeScript single-page app that
talks only to the server's JSON API (see [server.md](server.md)).

## Prerequisites

- Node.js 22.12 or newer (with npm).
- [uv](https://docs.astral.sh/uv/), to run the backend.

## Installing

From `frontend/`:

```sh
npm install
```

## Running it during development

Start the backend from `backend/`, telling it that the browser reaches the app at the
Vite address:

```sh
LINERSYNC_PUBLIC_URL=http://127.0.0.1:5173 LINERSYNC_ADMIN_PASSWORD='choose-a-password' uv run linersync serve
```

Then start Vite from `frontend/`:

```sh
npm run dev
```

Open <http://127.0.0.1:5173>. Vite serves the app and forwards every `/api` request to
the backend at `http://127.0.0.1:8000` (set `LINERSYNC_API` to use another address).

**Why the public URL is the Vite address.** The browser only ever talks to Vite, so
`http://127.0.0.1:5173` is the address it uses:

- The server refuses changes (403) whose `Origin` header differs from
  `LINERSYNC_PUBLIC_URL`. The Vite proxy passes the browser's `Origin` through unchanged,
  so the public URL must be the Vite address or every save fails.
- Service sign-in redirects back to `<public URL>/api/connections/oauth/callback`. With
  the Vite address, the redirect lands in the browser's tab and Vite forwards it to the
  backend. Use `127.0.0.1`, not `localhost`: Spotify only accepts loopback redirects to
  `127.0.0.1`.

## Scripts

Run from `frontend/`:

| Script | What it does |
|---|---|
| `npm run dev` | Starts the Vite dev server at `http://127.0.0.1:5173`. |
| `npm run build` | Type-checks and builds the app into `dist/`. |
| `npm run preview` | Serves the built `dist/` locally. |
| `npm test` | Runs the unit and component tests (Vitest) in watch mode. |
| `npm run lint` | Runs ESLint. |
| `npm run typecheck` | Runs the TypeScript compiler (`tsc -b`). |
| `npm run api:gen` | Regenerates `src/api/schema.d.ts` from `openapi.json`. |
| `npm run api:check` | Fails if `src/api/schema.d.ts` is out of date. |
| `npm run check` | The full check: type check, lint, tests and `api:check`. |
| `npm run e2e` | Runs the end-to-end tests (Playwright). |

## Regenerating the API types

Every API type comes from the server's OpenAPI schema. The schema is committed as
`frontend/openapi.json`, and a backend test fails when it differs from the app. After
changing the API, regenerate both files:

```sh
# from backend/
uv run python -m linersync.server.openapi > ../frontend/openapi.json
# from frontend/
npm run api:gen
```

Commit `openapi.json` and `src/api/schema.d.ts` together.

## End-to-end tests

The end-to-end tests drive the app in Chromium against the real backend with three fake
music services (`backend/tests/e2e_server.py`). Install the browser once:

```sh
npx playwright install chromium
```

Then, from `frontend/`:

```sh
npm run e2e
```

Playwright starts the fake backend on port 8765 and Vite on port 5173 (both ports must
be free), signs in and connects two fake services, and runs the flows in order with one
worker. Each run starts from an empty data folder. On a failure, the trace is kept in
`test-results/`; open it with `npx playwright show-trace <path to trace.zip>`.

## Serving the built app

For now the app runs only through Vite. Serving the built app from the LinerSync server
(and the Docker image) comes in a later release.
