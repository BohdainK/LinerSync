# Respect Destination Edits, Re-add and Copy to Source — Implementation Plan (Part 2 of 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Full syncs keep the user's destination-side deletions, additions and order, while still mirroring the source's own changes. The sync page lists destination deletions with a Re-add button, and destination additions with a one-time "Copy to source". A new sync onto a non-empty playlist asks whether to Merge or Overwrite.

**Architecture:** Ownership comes from the last write. A song on the destination is **sync-owned** if it matches a `last_synced.entries` pair; anything else is the user's and is never removed. Order follows the source, except for songs the user moved ("pinned") and songs the user added. Those stay anchored after the song that precedes them on the destination. All of this lives in the pure planner. The runner stores the extra state (last written order, pinned songs, and the removed/added lists as Tracks) on `MemberState`. Copy to source is a new previewed job that adds the songs to the source, pairs them with the existing destination copies, and then runs the sync.

**Tech Stack:** As in Part 1.

**Spec:** `docs/superpowers/specs/2026-11-03-preview-first-and-destination-edits-design.md`

**Depends on:** Part 1 (`2026-11-03-preview-first-and-swaps.md`): `preview_run_id` seeding, `PickChoiceIn`, `remember_picks`, `usePicks`, `MatchEditingProvider`, `ChangeMatch`.

**One decision differs from the spec's "calls made by me":** with per-song pinning, new source songs land at their **source position** (relative to unpinned songs), not at the end. The end-of-list rule existed only for the "freeze the whole order" fallback, which this plan doesn't need.

## Global Constraints

- All Part 1 global constraints apply: no `git commit`, the check commands, OpenAPI regeneration, lenient decoding with no migration, and layering.
- Exact UI copy:
  - Section headings: **"Removed on {destination}"**, **"Added on {destination}"**
  - Buttons: **"Re-add"**, **"Copy to source"**, **"Copy all to source"**, **"Copy to {source}"** (the start button after the copy preview)
  - Existing-playlist warning: **"This playlist already has {n} songs."** with the options **"Merge"** ("Keep its songs; they'll count as yours") and **"Overwrite"** ("Make it match the source on the first run"). Merge is the default.
  - Not-copied reason: **"not found on {source}"**.
- Add-only syncs: planning is unchanged. They still get the Removed/Added lists, Re-add and Copy to source.

## Review Focus

1. **Duplicates**: the same target id twice on the destination, only one of which the sync wrote. The extra copy is the user's and is kept; the sync's copy follows the source rules. Test: `test_full_keeps_user_duplicate_of_owned_song` (Task 2).
2. **A swap from Part 1 replacing a pinned song.** The new version must take the old one's place, and the old one must be removed rather than kept as a "user addition". Test: `test_swap_replaces_owned_song_in_place` (Task 2).
3. **Legacy state** (`last_synced` saved before this change: no `target_order`). No song may be removed that the sync didn't write, and no reorder may be treated as user pinning. Test: `test_legacy_state_keeps_unowned_songs_and_pins_nothing` (Task 2).
4. **A service without REORDER.** No reorder operation is produced, and new songs are appended, as today. Test: `test_full_without_reorder_appends_and_keeps_user_order` (Task 2).
5. **Copy to source when the song is already in the source** (the user copied it twice, or it was added there by hand meanwhile). There must be no duplicate add; it is only paired. Test: `test_copy_back_pairs_song_already_in_source` (Task 5).

---

### Task 1: Order helpers — pinned detection and anchored arrangement

**Files:**
- Create: `backend/src/linersync/domain/planning/order.py`
- Test: `backend/tests/domain/planning/test_order.py`

**Interfaces:**
- Produces:
  - `moved(previous: Sequence[str], current: Sequence[str]) -> list[str]` returns the items of `current` that are outside a longest common subsequence of `previous` and `current`, in `current` order. It is multiset-aware: duplicates count separately. Ties go to the LCS that keeps earlier `current` items.
  - `arrange(current: Sequence[str], base: Sequence[str], anchored: Sequence[str]) -> list[str]`, where:
    - `base` is the source-ordered list of songs that follow the source;
    - `anchored` is a multiset of the `current` items that keep their place (pinned and user-added).

    Each anchored occurrence is placed right after the nearest preceding non-anchored item of `current` that is also in `base`. An occurrence with no such item goes at the front. Anchored items sharing the same anchor keep their `current` order. The result is `base` plus the anchored items:

```python
def arrange(current, base, anchored):
    left = Counter(anchored)
    in_base = Counter(base)
    after: dict[int | None, list[str]] = {}   # key: occurrence index in base, None = front
    seen_base: Counter[str] = Counter()
    anchor: int | None = None
    base_index = _occurrence_index(base)       # (item, nth) -> position in base
    for item in current:
        if left[item] > 0:
            left[item] -= 1
            after.setdefault(anchor, []).append(item)
        elif seen_base[item] < in_base[item]:
            anchor = base_index[(item, seen_base[item])]
            seen_base[item] += 1
    out = list(after.get(None, []))
    for i, item in enumerate(base):
        out.append(item)
        out.extend(after.get(i, []))
    return out
```

- [ ] **Step 1: Write the failing tests**

```python
def test_moved_finds_the_song_the_user_dragged():
    assert moved(["A", "B", "C", "D"], ["A", "C", "D", "B"]) == ["B"]

def test_moved_is_empty_when_order_kept_with_gaps():
    assert moved(["A", "B", "C"], ["A", "C"]) == []

def test_arrange_keeps_user_song_after_its_neighbour():
    # source now orders C before B; user added X after A
    assert arrange(["A", "X", "B", "C"], ["A", "C", "B"], ["X"]) == ["A", "X", "C", "B"]

def test_arrange_new_source_song_at_source_position():
    assert arrange(["A", "B"], ["A", "N", "B"], []) == ["A", "N", "B"]

def test_arrange_anchor_at_front():
    assert arrange(["X", "A"], ["A"], ["X"]) == ["X", "A"]

@given(...)  # hypothesis: result is a permutation of base + anchored; base order preserved
def test_arrange_is_base_plus_anchored(...): ...
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/domain/planning/test_order.py -q`
Expected: FAIL (ImportError).

- [ ] **Step 3: Implement `moved` and `arrange`** in `order.py`. `moved` uses a standard O(n·m) LCS table; playlists are at most a few thousand songs.

- [ ] **Step 4: Run the tests to verify they pass.** Expected: PASS. Then run mypy and ruff.

- [ ] **Step 5: Checkpoint.** Report `order.py` and its test for the user to commit as "Order helpers for respecting destination order".

---

### Task 2: Full-mode planning respects destination edits

**Files:**
- Modify: `backend/src/linersync/domain/operations.py`: add fields to `MemberPlan`
- Modify: `backend/src/linersync/domain/sync.py`: add fields to `MemberState`; add `SyncMember.mirror_once`
- Modify: `backend/src/linersync/domain/planning/planner.py`: `_plan_destination`, `plan_member`
- Test: `backend/tests/domain/planning/test_planner.py`

**Interfaces:**
- Consumes: `moved`, `arrange` (Task 1).
- Produces:
  - `MemberPlan` gains:
    - `user_removed: tuple[SyncedEntry, ...] = ()`: pairs the user deleted on the destination; the planner doesn't re-add them;
    - `user_added: tuple[str, ...] = ()`: destination ids the sync doesn't own, which are kept;
    - `pinned: tuple[str, ...] = ()`: owned ids the user moved, which keep their place.
  - `MemberState` gains:
    - `target_order: tuple[str, ...] = ()`: the destination's ids as verified after the write; `()` = legacy/unknown;
    - `pinned: tuple[str, ...] = ()`;
    - `removed_by_you: tuple[Track, ...] = ()`: source tracks;
    - `added_by_you: tuple[Track, ...] = ()`: destination tracks.
  - `SyncMember.mirror_once: bool = False`: when set, full mode plans the way it does today (strict mirror) for that run only.
  - `plan_member(...)` gains the keyword arguments `owned: Sequence[str] = ()` (the targets of the previous pairs, a multiset), `previous_order: Sequence[str] = ()`, `previous_pinned: Sequence[str] = ()` and `mirror: bool = False`.

**Rules (full mode, `mirror` False):**
1. Destination deletions: `_user_deleted` now runs for both modes. Its kept pairs go to `user_removed`.
2. Walk `cur` with three budgets: `desired`, then `owned`, then neither.
   - An occurrence counted against `desired` is kept.
   - One counted against `owned` (and not `protected`) is removed.
   - Anything else is the user's and goes to `user_added`. Protected candidates are kept but are **not** listed as `user_added`.
3. `pinned` = (`previous_pinned` still present in `cur` ∩ desired) ∪ `moved(previous_order ∩ kept-desired, cur ∩ kept-desired)`. When `previous_order` is empty (legacy state), the second term is skipped.
4. With REORDER, the final order = `arrange(after_add, base, anchored)`, where:
   - `after_add` = the kept items of `cur` followed by the added items;
   - `base` = the desired items not in `pinned` (multiset);
   - `anchored` = `pinned` + `user_added` + the protected extras.

   Removals, `Reorder` and `expected_after` work the way the existing `final + removed` positions trick does.

   Without REORDER, the order is `cur` minus removals plus adds appended. The existing "can't reorder" note is emitted only when the source order would have moved an unpinned song.
5. Metadata update: unchanged.
6. `mirror` True: today's full-mode code path, unchanged.

- [ ] **Step 1: Write the failing tests** (in `test_planner.py`, using its `group`/`snapshot`/`matched` helpers)

```python
def test_full_keeps_song_user_deleted():           # prev pairs a,b,c; cur A,C → no AddTracks(B); user_removed has b
def test_full_keeps_song_user_added():             # cur A,X,B,C; X unowned → no removal of X; user_added == ("X",)
def test_full_removes_song_removed_from_source():  # source a,c; owned B present → RemoveTracks(B)
def test_full_keeps_user_duplicate_of_owned_song():# cur A,A,B; owned A once → second A in user_added
def test_full_keeps_user_order_and_takes_source_reorder():
    # prev order A,B,C,D; user moved B to the end (cur A,C,D,B); source reorders D before C
    # expected_after == ("A","D","C","B"); pinned == ("B",)
def test_swap_replaces_owned_song_in_place():      # pair b→B, decision b→B9, cur A,B,C → expected A,B9,C; B removed
def test_legacy_state_keeps_unowned_songs_and_pins_nothing():
def test_full_without_reorder_appends_and_keeps_user_order():
def test_mirror_once_strict_mirrors():             # mirror=True: unowned X removed, order = source
def test_add_only_reports_user_lists():            # add-only: user_removed/user_added filled, ops unchanged
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/domain/planning/test_planner.py -q`
Expected: the new tests FAIL. Existing full-mode tests that encode strict mirroring also fail; update each one to either pass `mirror=True` or assert the new behaviour. Keep a single list of which tests changed and why, for the checkpoint report.

- [ ] **Step 3: Implement the rules.** `_plan_destination` passes `owned` = the targets of `pairs`, `previous_order`/`previous_pinned` from `dest.last_synced`, and `mirror=dest.mirror_once`.

- [ ] **Step 4: Run the tests to verify they pass.** Then run the full backend checks. Expected: green.

- [ ] **Step 5: Checkpoint.** Report the files and the list of changed tests for the user to commit as "Full syncs respect destination deletions, additions and order".

---

### Task 3: Runner stores the new state

**Files:**
- Modify: `backend/src/linersync/application/runner.py`: `_state`, `_source_state`, the place where an outcome's state is written to the member (also clear `mirror_once` there), and dry-run results unchanged
- Modify: `backend/src/linersync/application/job_guard.py`: `referenced_keys` includes `user_added` ids and the `user_removed` source keys
- Test: `backend/tests/application/test_runner_apply.py`

**Interfaces:**
- Consumes: Task 2's fields.
- Produces:
  - `_state(plan, snap, source)`:
    - `target_order = snap.track_ids`;
    - `pinned = plan.pinned`;
    - `removed_by_you` = the source `Track`s for `plan.user_removed` keys, looked up in the source snapshot (keys gone from the source are dropped);
    - `added_by_you` = the destination `Track`s from `snap` for `plan.user_added`.
  - `MemberState.owned: bool = True`, set from `source.meta.owned` in `_source_state`. Part 2 Task 4 uses it.

- [ ] **Step 1: Write the failing tests**

```python
async def test_applied_run_records_user_lists_and_order(world):
    # seed last_synced, user deletes B and adds X on the fake apple playlist, run
    state = world.repos.syncs.get(SYNC).destinations[0].last_synced
    assert [t.track_id for t in state.removed_by_you] == ["b"]
    assert [t.track_id for t in state.added_by_you] == ["X"]
    assert state.target_order == world.apple.playlists["A1"].track_ids

async def test_mirror_once_cleared_after_settled_run(world): ...
async def test_dry_run_leaves_state_untouched(world): ...
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/application/test_runner_apply.py -k "user_lists or mirror_once or untouched" -q`
Expected: FAIL.

- [ ] **Step 3: Implement** as specified in the Interfaces block.

- [ ] **Step 4: Run the tests to verify they pass.** Then run the full backend checks. Expected: green.

- [ ] **Step 5: Checkpoint.** Report the files for the user to commit as "Store destination edits on sync state".

---

### Task 4: API — Merge/Overwrite, user lists on the sync, Re-add

**Files:**
- Modify: `backend/src/linersync/server/api/schemas.py`:
  - `DestinationIn.existing: Literal["merge", "overwrite"] = "merge"`;
  - `DestinationOut` gains `removed_by_you: list[TrackOut] = []`, `added_by_you: list[TrackOut] = []`, `can_copy_to_source: bool = False`;
  - add `ReAddIn`.
- Modify: `backend/src/linersync/server/api/syncs.py`:
  - `_destination` sets `mirror_once = d.existing == "overwrite" and d.playlist_id is not None`;
  - `_destination_out` fills the lists from `last_synced`;
  - add `POST /api/syncs/{sync_id}/destinations/{member_id}/re-add`.
- Regenerate the OpenAPI file and frontend types.
- Test: `backend/tests/server/test_syncs_api.py`

**Interfaces:**
- Produces:
  - `ReAddIn(source_service: str, source_track_id: str)`.
  - `POST .../re-add` → 202 `JobOut`. It:
    1. drops that key's pairs whose targets are not in `target_order` from `last_synced.entries`;
    2. drops the track from `removed_by_you`;
    3. saves the sync;
    4. submits `SyncJob(sync_id, Trigger.MANUAL)`, labelled "Sync “{name}”".

    It returns 409 when busy (`_not_busy`), 404 for an unknown sync or member, and 422 if the song isn't in `removed_by_you`.
  - `can_copy_to_source` = the source connector has `Capability.ADD` **and** `group.source.last_synced` is either absent or has `owned` set.

- [ ] **Step 1: Write the failing tests**

- `test_overwrite_sets_mirror_once`
- `test_merge_is_default`
- `test_sync_out_lists_user_edits`
- `test_re_add_runs_sync_and_puts_song_back`: after the job, the song is on the destination and no longer listed
- `test_re_add_unknown_song_422`
- `test_cannot_copy_to_source_without_add`

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/server/test_syncs_api.py -k "overwrite or merge or user_edits or re_add or copy_to_source" -q`
Expected: FAIL.

- [ ] **Step 3: Implement** as specified in the Interfaces block, then regenerate the frontend types.

- [ ] **Step 4: Run the tests to verify they pass.** Then run the full backend checks and `npm run api:check`. Expected: green.

- [ ] **Step 5: Checkpoint.** Report the files for the user to commit as "Merge/Overwrite, destination edit lists and Re-add API".

---

### Task 5: Copy to source job

**Files:**
- Create: `backend/src/linersync/application/copy_back.py`
- Modify: `backend/src/linersync/domain/jobs.py`: `CopyToSourceJob`; `Job.kind` → `"copy_to_source"`; `Job.sync_id` covers it
- Modify: `backend/src/linersync/domain/runs.py`: `RunKind.COPY_TO_SOURCE = "copy_to_source"`
- Modify: `backend/src/linersync/application/jobs.py`: execute and list services
- Modify: `backend/src/linersync/server/api/syncs.py` and `schemas.py`: `CopyToSourceIn`, plus `POST /api/syncs/{sync_id}/destinations/{member_id}/copy-to-source`
- Regenerate the OpenAPI file and frontend types.
- Test: `backend/tests/application/test_copy_back.py`, `backend/tests/server/test_syncs_api.py`

**Interfaces:**
- Consumes: `SyncRunner.apply_member_plan`, `MatchService.resolve(..., seeded=...)`, `seed_from_run`, `remember_picks` (Part 1).
- Produces:
  - `CopyToSourceJob(sync_id: str, member_id: str, track_ids: tuple[str, ...], dry_run: bool = True, preview_run_id: str | None = None)`. An empty `track_ids` means all of `added_by_you`.
  - `CopyToSourceIn(track_ids: list[str] = [], dry_run: bool = True, preview_job_id: str | None = None, picks: list[PickChoiceIn] = [])`. A real copy requires `preview_job_id` from a dry copy of the same sync and member; otherwise it returns 422. Picks are remembered keyed by (destination track key, source service).
  - `CopyBackService(deps, runner).copy(group, member_id, track_ids, *, dry_run, seed) -> SyncRun` does the following:
    1. Reads the source and the destination playlist.
    2. Takes the chosen destination tracks that are still not sync-owned.
    3. Matches them onto the source service with `MatchService.resolve`, over a `PlaylistSnapshot` built from those tracks.
    4. Plans member `src`: one `AddTracks` with the matched ids not already in the source (appended). Unmatched songs become `UnresolvedEntry(member_id="src", ...)`. `NotOnService` becomes a `ChangeNotMade("add", track_id, "not found on {source}")`.
    5. On a dry run, it returns the run without writing anything.
    6. On a real run, it calls `apply_member_plan`. On success it:
       - adds `SyncedEntry(TrackKey(source_service, matched_id), dest_track_id)` to the destination's `last_synced.entries`;
       - removes those tracks from `added_by_you`;
       - remembers `RememberedMatch(that source key, dest service, dest_track_id)`;
       - saves the group;
       - runs `runner.run_group(... kind=RunKind.SYNC ...)`, so the other destinations get the songs.
    7. It returns the copy run. The job's `run_ids` lists the copy run, then the sync run.

- [ ] **Step 1: Write the failing tests**

```python
async def test_copy_back_dry_run_plans_adds_and_writes_nothing(world): ...
async def test_copy_back_adds_to_source_pairs_and_syncs_others(world):
    # two destinations; X added by user on dst0 → after copy: X's source match in source,
    # dst0 still has exactly one X (paired, no duplicate), dst1 gained it
async def test_copy_back_pairs_song_already_in_source(world): ...   # no AddTracks, still paired
async def test_copy_back_not_found_is_reported(world): ...           # not_made reason "not found on Spotify"
def test_copy_to_source_requires_preview_for_real_copy(client): ...  # 422
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/application/test_copy_back.py tests/server/test_syncs_api.py -k copy -q`
Expected: FAIL.

- [ ] **Step 3: Implement** as specified in the Interfaces block, then regenerate the frontend types.

- [ ] **Step 4: Run the tests to verify they pass.** Then run the full backend checks and `npm run api:check`. Expected: green.

- [ ] **Step 5: Checkpoint.** Report the files for the user to commit as "Copy destination additions to the source".

---

### Task 6: Frontend — Merge/Overwrite warning on new syncs

**Files:**
- Modify: `frontend/src/features/new-sync/DestinationRows.tsx`: when a chosen existing playlist has `track_count > 0`, show the warning box with the Merge/Overwrite radio (copy as in Global Constraints); the row state gains `existing: "merge" | "overwrite"`
- Modify: `frontend/src/features/new-sync/NewSyncPage.tsx`: send `existing` in each `DestinationIn` (it is part of the preview input, so switching re-previews)
- Test: `frontend/src/features/new-sync/NewSyncPage.test.tsx`

- [ ] **Step 1: Write the failing tests**

- `"existing playlist with songs asks merge or overwrite"`: Merge is checked by default and the preview body has `existing: "merge"`.
- `"overwrite is sent and re-previews"`.
- `"empty existing playlist shows no warning"`.

- [ ] **Step 2: Run them to verify they fail**

Run: `npx vitest run src/features/new-sync`
Expected: FAIL.

- [ ] **Step 3: Implement** the warning box and the `existing` field.

- [ ] **Step 4: Run the tests to verify they pass.** Then run `npm run check`. Expected: green.

- [ ] **Step 5: Checkpoint.** Report the files for the user to commit as "Ask merge or overwrite for existing playlists".

---

### Task 7: Frontend — sync page lists, Re-add, Copy to source

**Files:**
- Create: `frontend/src/features/syncs/DestinationEdits.tsx`: per destination with any items, shows:
  - "Removed on {destination}": `TrackList` rows, each with a "Re-add" button;
  - "Added on {destination}": rows with checkboxes, plus "Copy to source" (for the selected songs) and "Copy all to source". Both are shown only when `can_copy_to_source` is true.
- Create: `frontend/src/features/syncs/CopyToSourcePanel.tsx`:
  - posts a dry copy, shows the run's results inside `MatchEditingProvider`, so unmatched or ambiguous songs get `ChangeMatch`;
  - then "Copy to {source}" posts the real copy with `preview_job_id` and `picks.toApi()`;
  - then shows the job's progress and both runs' results.
- Modify: `frontend/src/features/syncs/SyncPage.tsx`: render `DestinationEdits` under the destinations table
- Modify: run history/labels (`RunHistory.tsx`, `copy.ts`) for `RunKind` `copy_to_source` ("Copied to source")
- Test: `frontend/src/features/syncs/SyncPage.test.tsx`

**Interfaces:**
- Consumes: `DestinationOut.removed_by_you`, `added_by_you` and `can_copy_to_source`, `POST .../re-add`, `POST .../copy-to-source` (Tasks 4–5), and Part 1's picks components.

- [ ] **Step 1: Write the failing tests**

- `"lists songs removed and added on the destination"`
- `"re-add posts the song and shows the job"`
- `"copy all previews then copies with picks"`
- `"copy buttons hidden when the source can't take songs"`

- [ ] **Step 2: Run them to verify they fail**

Run: `npx vitest run src/features/syncs`
Expected: FAIL.

- [ ] **Step 3: Implement** `DestinationEdits`, `CopyToSourcePanel` and the run label, as specified in the Files block.

- [ ] **Step 4: Run the tests to verify they pass.** Then run `npm run check` and the full backend checks. Expected: green.

- [ ] **Step 5: Checkpoint.** Report the files for the user to commit as "Sync page: re-add and copy to source".
