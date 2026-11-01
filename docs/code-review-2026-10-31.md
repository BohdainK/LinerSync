# Code review: readability and simplicity (2026-10-31)

Scope: all production code in `backend/src/linersync` and `frontend/src`. Comments, generated files
(`schema.d.ts`, `openapi.json`) and vendored shadcn components were ignored. Performance, bugs and
test coverage were out of scope.

Baseline before changes: 1670 backend tests, 220 frontend tests, ruff, mypy (strict), tsc and
eslint all green.

## Overall picture

The architecture (domain / application / ports / adapters / server) is sound, and the code is
consistent within each module. The readability cost is almost all **duplication across siblings**:
the same idea written once per connector, per router, per repository or per page. That makes a reader
check whether each copy really behaves the same. A second, smaller theme is a handful of **long
methods** (`runner._apply_member`, `jobs._execute`, `NewSyncPage`, `SetupStepper`) that mix several
jobs.

Status legend: **Done** = applied in this round, **Skipped** = left with reason, **Open** = for a
later round.

---

## 1. Domain and application layer

| # | Where | Finding | Proposed change |
|---|---|---|---|
| A1 | `application/runner.py` `_apply_member` | 113 lines, 4 levels deep; redundant `brake: bool` param (always equals `replan is not None`) | Drop `brake`; extract op loop (`_run_ops`) and verify/result (`_verified_outcome`) |
| A2 | `runner._verify` | Returns a 3-tuple the caller re-derives with if/elif | Return `(snap, error)`; derive the rest at the call site |
| A3 | `runner._plan` / `_replan` | Build the same `PlanRequest` | Shared `_plan_members(...)` |
| A4 | runner ×3, `undo.py`, `backups.py` | "read, fail if incomplete" written 5×; backups re-implements `shortfall()` | One helper; reuse `shortfall()` |
| A5 | runner ×3, `attention.py` | Four hand-written scans of open attention items; two hand-built `AttentionItem(...)` | `_open_items(kind, **match)`; route construction through `_attention` |
| A6 | `export_json.restore` | Five identical "exists? save; count" blocks | Generic `_merge` helper |
| A7 | `jobs.recover` / `runner._abort` | Same "fail unfinished members" logic | One shared function |
| A8 | `jobs.py` | Three active-job scans with isinstance predicates | `_find_active(pred)` |
| A9 | `jobs._execute` | Sync group fetched 3×; hand-built ordered sets | Pass group through; `dict.fromkeys` |
| A10 | `jobs.py` | Two `except` branches with identical bodies | Merge |
| A11 | `jobs._back_up` | Loop-invariant closure redefined per iteration; default-arg binding trick | Hoist; factory method |
| A12 | `jobs.py` | "append run_id if new" ×2 | One helper |
| A13 | `jobs.py` | `guard.wrote = not guard.cancellable` reaches into guard state | `JobGuard.reset_writes()` |
| A14 | `runner.py` / `domain/runs.py` | Phase `Literal` defined twice | `RunPhase` in domain |
| A15 | runner, undo, planner | `PlaylistDraft(meta.name, meta.description, meta.cover_url)` ×3 | `PlaylistMeta.draft()` |
| A16 | `matching/normalize.py` | `_classify` / `_classify_dash` duplicate feat match and tag loop | Shared `_featured`, `_find_tags` |
| A17 | `backups._detail`, `matching_service` | `e.message or str(e)` ×2 | `ConnectorError.detail` |
| A18 | `stats.window_start`, `ports/quota.quota_day_start` | Same local-midnight-as-UTC math | One function |
| A19 | various | Unused `__all__`, `_or_now`, pointless alias, duplicated `error` attr | Remove |
| A20 | `domain/sync.py` + server + application | `"src"` / `f"dst{n}"` member ids are magic strings across layers | `SOURCE_MEMBER_ID`, `destination_member_id(n)` |
| A21 | `domain/operations.py` + 7 connectors | `_Op` union redefined 7× | One `PlaylistOp` alias |

Open for later: `planner.plan_member` (~107 lines) could split its "missing playlist" and "add-only"
branches into helpers.

## 2. Connectors (`adapters/connectors`, `kit`)

The `kit` package exists, but much shared logic never moved into it.

| # | Where | Finding | Proposed change |
|---|---|---|---|
| C1 | `spotify/moves.py` | Re-implements `kit.ordering.occurrence_labels` / `lis_indexes`; all three `moves.py` repeat the same setup | Import from kit; `stable_occurrences()` helper |
| C2 | every `plugin.py` | 9-keyword `HttpClient(...)` constructor copied ~10×; artwork hasher line ×6 | `kit.plugin.http_client(ctx, ...)`, `artwork_hasher(...)` |
| C3 | every connector `_entries` | Same artwork-hash lookup, URL computed twice | `kit.images.hashes_for(hasher, urls)` |
| C4 | all 6 connectors | Same error message templates rebuilt per service (8 kinds) | `kit/errors.py` `Errors(service, name)` factory |
| C5 | 5 connectors | ReadFailed/HttpFailure → WriteFailed conversion duplicated, sometimes twice per file | One helper + one `except` |
| C6 | spotify, tidal | Hand-rolled `sent` flag loops duplicate `apply_in_batches` | Use `apply_in_batches(items, 1, send)` |
| C7 | 4 sites | Removal position check copied | `kit.ordering.at_positions(...)` |
| C8 | spotify / tidal | PKCE authorize URL, redirect check, plugin `authorize()` duplicated | Shared in `kit/oauth.py`, thin wrappers kept |
| C9 | spotify, tidal, kit, 3 plugins | ISO datetime / login-expiry parsing duplicated | `kit/parsing.py` |
| C10 | 6 connectors | "response must be a JSON object" guard ×6 | `kit.http.json_object()` |
| C11 | tidal, apple, qobuz | `_attributes` / `_attrs` / `_mapping` accessors | `kit.parsing.as_mapping`, `attributes` |
| C12 | 4 plugins | Same `test()` failure mapping; ~10 `try/finally aclose` blocks | `connection_failed()`; `HttpClient` as async context manager |
| C13 | `tidal/plugin.py` | Two `TidalAuth` objects for one sign-in | Return auth from `_clients` |
| C14 | all connectors | Positional `ConnectorLimits(50, 100, 2)` shadowed by `_PAGE`/`_BATCH`; `_SEARCH_LIMIT` ×6 | Keyword args; use `LIMITS.*`; one constant |
| C15 | tidal `_follow` / apple `_next` | Same "follow next link keeping params" | **Skipped**: medium risk (Apple paging/repeat detection) |
| C16 | 7 files | `_Op` union redefined | See A21 |
| C17 | spotify | Two constants for the same `ServiceId`; constants in odd module | One `SERVICE` in `parse.py` |
| C18 | youtube_music, qobuz | `_fingerprint` duplicated | Move to kit |
| C19 | `qobuz._read` | 47-line loop re-parses first page each iteration | Parse once; extract overlap check |
| C20 | youtube_music `apply`, jellyfin | Mixed dispatch style; repeated `/Items` query | `_add` / `_update_metadata`; query helper |

## 3. Persistence and server

| # | Where | Finding | Proposed change |
|---|---|---|---|
| S1 | `server/api/syncs.py`, `runs.py` | Shared route helpers live in sibling routers, so routers import each other | `server/api/common.py`; track views to schemas |
| S2 | 6 routers | Lookup-or-404 helper written 8× | One generic `found(value, detail)` |
| S3 | `adapters/sqlite/*` | Four upsert styles (update-then-insert, on_conflict, delete+insert, OR REPLACE) | `db.upsert(...)`; keep the deliberate OR REPLACE / seq cases |
| S4 | `adapters/sqlite/*` | "select body by key, decode" ~10×, list variant ~8× | `load_one` / `load_all` |
| S5 | connections, transfers routers | ConnectorError → 502 duplicated | `connector_failed(...)` |
| S6 | home, backups, app, syncs; backups/stats | "set-up services" filter ×4; `_display_name` ×2 | `ConnectionManager.set_up()`; one name helper |
| S7 | `server/connections.py` | `_mark_failed_start` ≈ `on_auth_required` | `_mark(service, status, message)` |
| S8 | `server/connections.py` | Plugin-kind isinstance check ×3 | Generic `_plugin_as(service, kind, how)` |
| S9 | `server/api/connections.py` | 4 identical `ValueError → 409` blocks; inline kind check | `WrongSetup` error type |
| S10 | schemas / routers | Domain→schema mapping split between `.of` methods and loose functions | `.of` everywhere |
| S11 | `syncs.patch_sync` | 40-line handler, mode check done in two passes | `_patched_members(...)` |
| S12 | `app.py`, syncs, settings | Schedule reconcile and cron validation duplicated | One reconcile; public `check_schedule` |
| S13 | `adapters/scheduler.py` | Per-job options duplicate `job_defaults`; trigger parse ×2 | Drop; `_trigger(...)` |
| S14 | `server/api/export.py` | `"archive/"` prefix added, stripped, re-parsed | Carry an `archived` flag |
| S15 | `syncs._sync_out` | Local `rebuilt` feeds param `missing` | Rename; `_missing_destinations()` |
| S16 | `runs._member` | 7 inline `None if result is None else ...`; untyped place tuple | Branch once; `Place` NamedTuple |
| S17 | `devtools/live_login.py`, memory adapters | Duplicate `SystemClock`; spotify/tidal login twins; dead `or` | Reuse; fold; remove |

## 4. Frontend

| # | Where | Finding | Proposed change |
|---|---|---|---|
| F1 | `NewSyncPage` / `NewTransferPage` | Bulk estimate → warning → start flow copied | `useBulkEstimate` hook |
| F2 | `NewSyncPage.tsx` (384 lines) | Row model, row component, list logic, scope radio and submit in one file | `DestinationRows.tsx`, `ScopeChoice` |
| F3 | 3 files | Mode `<select>` written 3× | `ModeSelect` |
| F4 | both New pages | `?source=` preset logic duplicated | `useSourceSelection()` |
| F5 | `SetupStepper.tsx` | Sign-in success handling duplicated; alias of `test.mutate`; large `Steps` | `StepItem.tsx`, `useOAuthReturn`, `StepContent` |
| F6 | 19 sites | Same `ErrorNotice` + retry JSX | `QueryError({ query })` |
| F7 | 6 sites | Inline `/api/syncs`, `/api/settings`, `/api/runs/{id}` queries | `useSyncs`, `useSettings`, `useRun` |
| F8 | SyncPage, RunPage | "on job end, invalidate" effect re-implements `isActive` with strings | `useOnJobEnd` |
| F9 | 8 sites | `status !== "not_set_up"`, `capabilities.includes("add")` | `isSetUp`, `canAdd` |
| F10 | PreviewPanel, RunPage, MemberSection | `songs()` ×2, unresolved-track rendering ×2 | `formatSongs`, `UnresolvedItem` |
| F11 | 5 sites | Change-count and backup-result strings built inline | `formatChangeCounts`, `formatBackupResult` |
| F12 | 4 files | `""` = "new playlist" magic string | `NEW_PLAYLIST_ID`, `chosenPlaylistId()` |
| F13 | `FormStep.tsx` | Blank-secrets map ×2 | `blankSecrets()` |
| F14 | `SettingsPage.tsx` | Numeric fields listed in 5 places | One field table |
| F15 | `RequireSession.tsx` | Login redirect ×2 | One const |
| F16 | both New pages | Notes `<ul>` ×2 | `NotesList` |
| F17 | 16 sites | Inline-link class string copy-pasted | Shared constant |
| F18 | various | Dead exports; unused `exclude` / `limit` props | Remove |
| F19 | various | Small awkward expressions | Simplify |
| F20 | ~22 sites | Section + heading with hard-coded ids; `StatsPage` already has a `Section` | Shared `Section` |

`api/types.ts` has 27 unused aliases. They were left as a deliberate mirror of the schema.

---

## Outcome of this round

All findings above were applied except the ones listed below. Nothing is committed.

**Verification after the round**
- Backend: `pytest` gives 1670 passed, 15 skipped. `ruff check`, `ruff format --check` and `mypy --strict` are clean.
- The generated OpenAPI is byte-identical to `frontend/openapi.json`, so HTTP shapes are unchanged.
- Frontend: `npm run check` passes (tsc, eslint, 220 vitest tests, api:check).
- No existing test assertion had to change. One import in `tests/adapters/connectors/spotify/test_parse.py` was updated (`SPOTIFY` → `SERVICE`).

**Size**
- Tracked files: −3073 / +2278 lines. There are also about 790 lines in 20 new files: shared helpers, `kit/errors.py`, `kit/parsing.py`, `server/api/common.py`, and new hooks and components.
- Total size is roughly flat. The gain is in duplication removed and smaller units, not in line count:
  - `NewSyncPage` went from 384 to 234 lines.
  - `SetupStepper` went from 251 to 134.
  - `runner.py` grew slightly (852 to 890) because `_apply_member` was split into named steps.

**Skipped or partial**
- A4: the `backups.py` copy was not changed. Reusing `shortfall()` would change a message that a test asserts.
- C15: tidal `_follow` and apple `_next` were not merged. The Apple paging and repeat-page detection make this medium risk.
- F18: the ServiceSelect `exclude` prop was kept because `PlaylistPicker.test.tsx` uses it.
- `ApprovalRepository.is_approved` was kept: it is used only by contract tests, but it is part of the port.

**Small behaviour-adjacent changes to be aware of**
- The scheduler log line for an invalid backup schedule now reads "skipping backup: invalid schedule …". No test or UI depends on it.
- Apple parse: an `attributes` value that is not an object used to raise an uncaught AttributeError. It now reads as empty. Valid data is unaffected.
- Jellyfin's internal "not a JSON object" ValueError text changed. It is never shown to users.
- `server/connections.py` `_plugin_as` needs `# type: ignore[type-abstract]` at 3 call sites. This is a mypy limitation with Protocol classes in `type[P]`.

**Open for a next round**
- `planner.plan_member` (~107 lines): extract the missing-playlist and add-only branches.
- C15, once the Apple paging has a focused test.
- `runner.py` is still the largest module. Splitting the apply/verify part into its own module would be the next structural step.
- `api/types.ts` holds 27 unused schema aliases. Decide whether it is meant to be a full mirror of the schema.
