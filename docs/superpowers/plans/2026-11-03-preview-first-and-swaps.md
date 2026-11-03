# Preview-first Transfers and Syncs with Match Swapping — Implementation Plan (Part 1 of 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every transfer and every new sync is previewed first (unless the user picks "Transfer immediately"). In the preview the user can swap any linked song. "Start transfer" / "Start syncing" then performs exactly what the preview showed, with the swaps applied.

**Architecture:** The real job carries the preview run's id. The runner seeds matching from that run's pairs, so the real run reuses the preview's decisions instead of searching again. This is cheaper and keeps the real run identical to the preview. Swaps travel with the start request and are saved as `RememberedMatch`es before the job is submitted. Remembered matches already beat every other source in `MatchService`, so they win over the seed and also reach existing syncs. The frontend keeps the user's swaps in a small picks store, which a React context shares with the existing run-result components.

**Tech Stack:** Python 3.14, FastAPI, pytest/hypothesis, mypy --strict, ruff (backend); React 19, TanStack Query, Vitest + MSW, openapi-typescript (frontend).

**Spec:** `docs/superpowers/specs/2026-11-03-preview-first-and-destination-edits-design.md`

Part 2 (`2026-11-03-respect-destination-edits.md`) builds on this plan's picks store, `ChangeMatch` component and `preview_run_id` plumbing.

## Global Constraints

- **Never run `git commit`** (user rule). Each "Checkpoint" step lists the files for the user to commit; the executor stops there and reports them.
- Backend checks, run from `backend/`: `uv run pytest -q`, `uv run mypy --strict src tests`, `uv run ruff check src tests`.
- Frontend checks, run from `frontend/`: `npm run check` (tsc, eslint, vitest, api:check).
- After any API schema change, regenerate the frontend's types. From `backend/`: `uv run python -m linersync.server.openapi > ../frontend/openapi.json`. Then from `frontend/`: `npm run api:gen`.
- Stored bodies are decoded leniently (`adapters/sqlite/codec.py`), so new dataclass fields with defaults need no migration.
- Exact UI copy:
  - Toggle: **"Transfer immediately (skip the preview)"**
  - Buttons: **"Start transfer"**, **"Start syncing"**, **"Change"**, **"Use this"**, **"Search"**
- Layering is enforced by `backend/tests/test_layers.py`: domain imports nothing from application/server.

## Review Focus

1. **The preview is stale by the time you press Start.** For example, the source playlist changed between preview and start. The real run must still apply the preview's pairs for songs it seeded, and match any new songs normally. Test: `test_seeded_run_matches_new_source_songs_normally` (Task 1).
2. **A preview run id that doesn't belong to the request** (a different source playlist, a non-dry run, or an unknown id) must be rejected with 422 and must not silently run unseeded. Test: `test_from_preview_rejects_foreign_run` (Task 2).
3. **Swapping the same source song twice** (or in two playlists of a bulk preview) must send only the last pick. Test: `usePicks › last pick for the same song wins` (Task 4).
4. **Changing the form after a preview** (different destination or playlist) must drop the old picks and old preview, so "Start syncing" never sends picks for another selection. Test: `NewSyncPage › changing the source clears picks` (Task 6).
5. **A partly failed bulk preview** (one transfer had no run) must refuse to start rather than mis-pairing runs with transfers. Test: `test_from_preview_requires_every_run` (Task 2).

---

### Task 1: Seed matching from a preview run

**Files:**
- Modify: `backend/src/linersync/application/matching_service.py` (`MatchService.resolve`, `_resolve_one`)
- Modify: `backend/src/linersync/application/runner.py` (`run_group`, `_Work`, `_match`)
- Modify: `backend/src/linersync/domain/jobs.py` (`SyncJob`, `TransferJob`: new field)
- Modify: `backend/src/linersync/application/transfer.py` (`TransferService.transfer`)
- Modify: `backend/src/linersync/application/jobs.py` (`_execute`: load the seed)
- Test: `backend/tests/application/test_matching_service.py`, `backend/tests/application/test_runner_read.py`, `backend/tests/application/test_jobs.py`

**Interfaces:**
- Produces:
  - `MatchService.resolve(source, target, previous, seeded: Mapping[TrackKey, str] = {}) -> dict[TrackKey, MatchDecision]`. Order: same service → remembered → **seeded** → previous sync → search. A seeded hit returns `Matched(tid, 1.0, (), MatchVia.PREVIOUS_SYNC)`.
  - `SyncJob.preview_run_id: str | None = None` and `TransferJob.preview_run_id: str | None = None`.
  - `SyncRunner.run_group(..., seed: Mapping[ServiceId, Mapping[TrackKey, str]] | None = None)`.
  - `TransferService.transfer(..., seed: Mapping[ServiceId, Mapping[TrackKey, str]] | None = None)`.
  - `seed_from_run(run: SyncRun, services: Mapping[str, ServiceId]) -> dict[ServiceId, dict[TrackKey, str]]` in `application/runner.py`. It reads each member plan's `synced` entries, keyed by that member's service. The first pair per key wins.

- [ ] **Step 1: Write the failing tests**

```python
# test_matching_service.py
async def test_seeded_pair_beats_search_but_not_remembered(...):
    # seeded {key(a): "A2"}; remembered key(b) -> "B9"; seeded also has key(b) -> "B2"
    out = await svc.resolve(source, target, None, seeded={key_a: "A2", key_b: "B2"})
    assert out[key_a] == Matched("A2", 1.0, (), MatchVia.PREVIOUS_SYNC)
    assert out[key_b].target_track_id == "B9"
    assert not any(c.startswith("search") for c in target.calls if "a" in c)

# test_runner_read.py
async def test_seeded_run_matches_new_source_songs_normally(world):
    # source has a, b, c; the seed only covers a -> "A2" (not what search would find)
    run, _ = await runner.run_group(group, kind=..., trigger=..., dry_run=True,
                                    persist=False, seed={APPLE: {key_a: "A2"}})
    synced = {e.source_key: e.target_track_id for e in run.plan.member_plans[0].synced}
    assert synced[key_a] == "A2"
    assert synced[key_b] == "B" and synced[key_c] == "C"  # searched as usual

# test_jobs.py
async def test_transfer_job_with_preview_run_reuses_its_pairs(...):
    # run a dry TransferJob, take run_ids[0], then submit TransferJob(..., preview_run_id=that)
    # assert created playlist holds the preview's targets and apple saw no search calls
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/application/test_matching_service.py tests/application/test_runner_read.py tests/application/test_jobs.py -k "seed or preview_run" -q`
Expected: FAIL (`unexpected keyword argument 'seeded'` / `'seed'` / `'preview_run_id'`).

- [ ] **Step 3: Implement**

`_match` passes `seed.get(service, {})` to `resolve`. In `JobService._execute`, when `request.preview_run_id` is set:
- load the run with `self._repos.runs.get`;
- build member→service from `self._contexts.get(run_id).members`;
- build the seed with `seed_from_run`.

An unknown or missing run fails the job with `_JobFailed("The preview is gone; preview again")`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: the Step 2 command. Expected: PASS. Then run the full backend checks. Expected: all green.

- [ ] **Step 5: Checkpoint.** Report the modified files for the user to commit as "Seed real runs from their preview's matches".

---

### Task 2: Start a transfer from its preview, with picks; alternatives endpoint

**Files:**
- Modify: `backend/src/linersync/server/api/schemas.py`: add `PickChoiceIn`, `FromPreviewIn`, `AlternativesIn`
- Modify: `backend/src/linersync/server/api/transfers.py`: add `POST /api/transfers/from-preview`
- Modify: `backend/src/linersync/server/api/runs.py`: add `POST /api/runs/{run_id}/alternatives`
- Modify: `backend/src/linersync/application/suggestions.py`: add `search_text`
- Create: `backend/src/linersync/application/picks.py`
- Modify: `frontend/openapi.json`, `frontend/src/api/schema.d.ts`, `frontend/src/api/types.ts` (export the new types)
- Test: `backend/tests/server/test_transfers_api.py` (create if missing; follow `test_syncs_api.py`'s fixtures), `backend/tests/server/test_runs_api.py`, `backend/tests/application/test_suggestions.py`

**Interfaces:**
- Consumes: `TransferJob.preview_run_id`, `BulkTransferJob` (Task 1).
- Produces:
  - `PickChoiceIn(source_service: str, source_track_id: str, target_service: str, target_track_id: str)`
  - `FromPreviewIn(preview_job_id: str, picks: list[PickChoiceIn] = [])`
  - `remember_picks(repo: MatchRepository, picks: Iterable[PickChoiceIn]) -> None` in `application/picks.py` (takes plain tuples, so it has no server import: `Iterable[tuple[TrackKey, ServiceId, str]]`)
  - `POST /api/transfers/from-preview` → 202 `JobOut`. The preview job must be a `TransferJob` or `BulkTransferJob` with `dry_run=True`, status `succeeded`, and exactly one run per transfer. The new job:
    - repeats each transfer with `dry_run=False` and `preview_run_id=run_ids[i]`;
    - has the label "Transfer to {name}" or "Transfer {n} playlists", as today.
  - `AlternativesIn(member_id: str, source_service: str, source_track_id: str, query: str | None = None)`
  - `POST /api/runs/{run_id}/alternatives` → `list[CandidateOut]`. The source track comes from the run's `RunContext.tracks`, and the target service from its `members`.
    - No `query`: returns `find_similar(source, connector, cfg)`.
    - With `query`: returns `search_text(...)`.
  - `search_text(source: Track, target: Connector, cfg: MatchConfig, text: str) -> tuple[ScoredCandidate, ...]`: searches `TrackQuery(text, ())`, scores each result with `score_candidate`, and returns them best first, capped at `MAX_SUGGESTIONS`, with **no** floor.

- [ ] **Step 1: Write the failing tests**

```python
async def test_from_preview_starts_real_transfer_with_picks(client, world):
    preview = start_dry_transfer(client)          # POST /api/transfers dry_run=True, wait
    r = client.post("/api/transfers/from-preview", json={
        "preview_job_id": preview["job_id"],
        "picks": [{"source_service": "spotify", "source_track_id": "b",
                   "target_service": "apple_music", "target_track_id": "B9"}]})
    assert r.status_code == 202
    job = wait(client, r.json()["job_id"])
    assert job["status"] == "succeeded"
    assert world.repos.matches.get(TrackKey(SPOTIFY, "b"), APPLE).target_track_id == "B9"
    assert created_playlist(world).track_ids == ("A", "B9", "C")

def test_from_preview_rejects_foreign_run(client):    # non-dry job, unknown id → 422
def test_from_preview_requires_every_run(client):     # bulk preview with a failed transfer → 409

async def test_alternatives_lists_similar_then_searches_text(client, world): ...
    # no query → candidates scored ≥ SUGGEST_FLOOR; query "Song B live" → fake search called with that title

async def test_search_text_keeps_low_scores(...):   # a 0.2-confidence result is returned
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/server/test_transfers_api.py tests/server/test_runs_api.py tests/application/test_suggestions.py -k "from_preview or alternatives or search_text" -q`
Expected: FAIL (404 route / ImportError).

- [ ] **Step 3: Implement**

Picks are remembered **before** `services.jobs.submit`, and only after every validation check has passed, so a rejected request saves nothing. Then regenerate the OpenAPI file and the frontend types (see Global Constraints) and export `PickChoiceIn`, `FromPreviewIn`, `AlternativesIn` and `CandidateOut` from `frontend/src/api/types.ts`, following its existing pattern.

- [ ] **Step 4: Run the tests to verify they pass**

Run: the Step 2 command, then the full backend checks, then `npm run api:check` in `frontend/`. Expected: all green.

- [ ] **Step 5: Checkpoint.** Report the files for the user to commit as "Start transfers from their preview with picks; alternatives endpoint".

---

### Task 3: Start a new sync from its preview; bulk sync preview

**Files:**
- Modify: `backend/src/linersync/domain/jobs.py`: add `BulkPreviewJob(groups: tuple[SyncGroup, ...])`; `Job.kind` → `"bulk_preview"`
- Modify: `backend/src/linersync/application/jobs.py`: execute `BulkPreviewJob` (one `runner.preview` per group, recording each run like `PreviewJob` does; the cancel check goes between groups, like `BulkTransferJob`); list its services
- Modify: `backend/src/linersync/server/api/schemas.py`: `SyncStartIn`; `SyncCreateIn.start`
- Modify: `backend/src/linersync/server/api/syncs.py`: `POST /api/syncs` and `/api/syncs/bulk` honour `start`; add `POST /api/syncs/preview/bulk`
- Regenerate the OpenAPI file and frontend types.
- Test: `backend/tests/server/test_syncs_api.py`, `backend/tests/application/test_jobs.py`

**Interfaces:**
- Consumes: `SyncJob.preview_run_id` (Task 1), `remember_picks` (Task 2).
- Produces:
  - `SyncStartIn(preview_run_id: str, picks: list[PickChoiceIn] = [])`
  - `SyncCreateIn.start: SyncStartIn | None = None`. When set, the server:
    1. checks that the preview run is a dry run whose source member matches `body.source`;
    2. saves the sync;
    3. remembers the picks;
    4. submits `SyncJob(sync_id, Trigger.MANUAL, preview_run_id=...)`.

    The existing `SyncOut` response gains `active_job` filled in. When `start` is absent, the behaviour is unchanged.
  - `POST /api/syncs/preview/bulk` (body `SyncBulkIn`) → 202 `JobOut` whose `run_ids[i]` is the preview of `syncs[i]`. The label is "Preview {n} syncs".

- [ ] **Step 1: Write the failing tests**

```python
async def test_create_with_start_runs_first_sync_from_preview(client, world):
    preview = wait(client, client.post("/api/syncs/preview", json=BODY).json()["job_id"])
    body = {**BODY, "start": {"preview_run_id": preview["run_ids"][0], "picks": [PICK_B]}}
    out = client.post("/api/syncs", json=body).json()
    job = wait(client, out["active_job"]["job_id"])
    assert job["kind"] == "sync" and job["status"] == "succeeded"
    assert world.apple.playlists["A1"].track_ids == ("A", "B9", "C")

def test_create_with_start_rejects_preview_of_other_source(client): ...  # 422, no sync saved
async def test_bulk_preview_returns_one_run_per_sync_in_order(client): ...
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/server/test_syncs_api.py tests/application/test_jobs.py -k "start or bulk_preview" -q`
Expected: FAIL.

- [ ] **Step 3: Implement** as specified in the Interfaces block, then regenerate the frontend types.

- [ ] **Step 4: Run the tests to verify they pass.** Then run the full backend checks and `npm run api:check`. Expected: green.

- [ ] **Step 5: Checkpoint.** Report the files for the user to commit as "Start new syncs from their preview; bulk sync preview".

---

### Task 4: Frontend picks store and the "Change" control

**Files:**
- Create: `frontend/src/features/matches/usePicks.ts`
- Create: `frontend/src/features/matches/MatchEditing.tsx` (context + provider + `useMatchEditing()`)
- Create: `frontend/src/features/matches/ChangeMatch.tsx`
- Modify: `frontend/src/features/runs/MatchList.tsx`: when editing context is present, each row shows the picked target (if any) and a `ChangeMatch`
- Modify: `frontend/src/features/runs/MemberSection.tsx`: pass `memberId` to `MatchList`
- Modify: `frontend/src/components/UnresolvedItem.tsx`: optional `action?: ReactNode` rendered under the candidates
- Modify: wherever `RunResults` renders unresolved items: pass a `ChangeMatch` as `action` when editing
- Test: `frontend/src/features/matches/usePicks.test.ts`, `frontend/src/features/matches/ChangeMatch.test.tsx`

**Interfaces:**
- Produces:
  - `type Pick = { runId: string; memberId: string; source: TrackOut; target: TrackOut }`
  - `usePicks(): { picks: Pick[]; pick(p: Pick): void; picked(runId: string, memberId: string, source: TrackOut): TrackOut | undefined; clear(): void; toApi(): PickChoiceIn[] }`
    - `toApi` dedupes by `(source.service, source.track_id, target.service)`. The last pick wins.
  - `<MatchEditingProvider runId={string} picks={ReturnType<typeof usePicks>}>`
  - `useMatchEditing(): { runId: string; picks: ReturnType<typeof usePicks> } | null`
  - `<ChangeMatch runId memberId source onPick={(t: TrackOut) => void} />` behaves as follows:
    - A "Change" button opens an inline panel.
    - On open, it POSTs `/api/runs/{run_id}/alternatives` without a query, then lists candidates, each with a "Use this" button.
    - A text box plus a "Search" button re-POSTs with `query`.
    - Picking calls `onPick` and closes the panel.

- [ ] **Step 1: Write the failing tests**

```ts
describe("usePicks", () => {
  it("last pick for the same song wins", () => {
    // pick(b→B2) then pick(b→B9) → toApi() has one entry with target_track_id "B9"
  });
  it("picked() returns the swapped target for that run and member only", ...);
});
describe("ChangeMatch", () => {
  it("lists alternatives and uses the chosen one", async () => {
    // MSW: POST */api/runs/r1/alternatives → [candidate B9]; click "Change", "Use this" → onPick(B9)
  });
  it("searches with the typed text", ...);  // request body has query "song b live"
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `npx vitest run src/features/matches`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement** as specified in the Interfaces block. Reuse `TrackList` rows for the candidates. In `MatchList`, a picked row shows the picked track as the target and gets a "your pick" badge, using `Badge` the way `TransferResult` does.

- [ ] **Step 4: Run the tests to verify they pass.** Then run `npm run check`. Expected: green.

- [ ] **Step 5: Checkpoint.** Report the files for the user to commit as "Picks store and Change control for previews".

---

### Task 5: Transfer page — preview first

**Files:**
- Modify: `frontend/src/features/transfers/NewTransferPage.tsx`
- Modify: `frontend/src/features/transfers/TransferResult.tsx`: wrap the results in `MatchEditingProvider` when the run is a dry run and a picks store is passed
- Modify: `frontend/src/copy.ts` if the page's strings live there
- Test: `frontend/src/features/transfers/NewTransferPage.test.tsx`

**Interfaces:**
- Consumes: `POST /api/transfers` and `/bulk` (`dry_run`), `POST /api/transfers/from-preview` (Task 2), and `usePicks`, `MatchEditingProvider` (Task 4).
- Behaviour:
  - The "Dry run" checkbox is replaced by the toggle "Transfer immediately (skip the preview)", off by default.
  - **Toggle off** (the default): starting sends `dry_run: true`. Once the preview job finishes, the results are shown with Change controls. A "Start transfer" button posts `from-preview` with `preview_job_id` and `picks.toApi()`. The page then shows that job's progress and results, as the current flow does.
  - **Toggle on:** sends `dry_run: false`, the same as today's flow.
  - The "Start another transfer" reset also clears the picks.

- [ ] **Step 1: Update the tests**

Rewrite `"dry run flag is sent"` as `"previews by default, then starts from the preview"`:
- the first POST body has `dry_run: true`;
- clicking "Start transfer" posts `/api/transfers/from-preview` with the picked song.

Add:
- `"transfer immediately skips the preview"`: the body has `dry_run: false` and no "Start transfer" step appears.
- `"bulk preview then start"`.

- [ ] **Step 2: Run them to verify they fail**

Run: `npx vitest run src/features/transfers`
Expected: FAIL.

- [ ] **Step 3: Implement** the behaviour in the Interfaces block.

- [ ] **Step 4: Run the tests to verify they pass.** Then run `npm run check`. Expected: green.

- [ ] **Step 5: Checkpoint.** Report the files for the user to commit as "Preview transfers by default".

---

### Task 6: New sync page — swap in the preview, "Start syncing"

**Files:**
- Modify: `frontend/src/features/new-sync/PreviewPanel.tsx`: each destination lists the songs it will add as `MatchList` (pairs with `added: true`) inside `MatchEditingProvider` (run id = the preview run), and unmatched songs get a `ChangeMatch` action
- Modify: `frontend/src/features/new-sync/NewSyncPage.tsx`:
  - the single create button becomes "Start syncing";
  - it is enabled only when the preview status is `ready`;
  - it sends `start: { preview_run_id, picks }`;
  - picks are cleared whenever the preview input key changes.

  For bulk: after the estimate dialog, post `/api/syncs/preview/bulk`, show one `PreviewPanel` per sync (the run at the same index), then "Start syncing" posts `/api/syncs/bulk`, where each sync carries `start` with its own run id and the picks for that run.
- Modify: `frontend/src/features/new-sync/usePreview.ts`: expose the run id it settled on, which the state already holds as `run.run_id`; no new request
- Test: `frontend/src/features/new-sync/NewSyncPage.test.tsx`

**Interfaces:**
- Consumes: `SyncCreateIn.start`, `POST /api/syncs/preview/bulk` (Task 3), and Task 4's components.

- [ ] **Step 1: Write the failing tests**

- `"start syncing sends the preview run and picks"`
- `"start syncing waits for the preview"` (disabled while it's running)
- `"changing the source clears picks"`
- `"bulk previews each sync then starts them"`

- [ ] **Step 2: Run them to verify they fail**

Run: `npx vitest run src/features/new-sync`
Expected: FAIL.

- [ ] **Step 3: Implement** the behaviour in the Files block.

- [ ] **Step 4: Run the tests to verify they pass.** Then run `npm run check`, and the full backend checks once more. Expected: green.

- [ ] **Step 5: Checkpoint.** Report the files for the user to commit as "Preview new syncs and start them from the preview".
