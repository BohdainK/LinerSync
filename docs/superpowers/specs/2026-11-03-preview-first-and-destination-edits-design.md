# Preview-first transfers and syncs, match swapping, and respected destination edits

## Why

The matcher sometimes links the wrong version of a song. The case that
prompted this: a track from a compilation album was picked instead of the
original. Today a transfer runs straight away unless you tick "Dry run", and
nothing in the app lets you change which song a track gets linked to. Once
it's wrong, it's wrong on the destination.

Separately, full syncs treat the destination as a mirror. Anything you change
there gets undone on the next run: a song you deleted comes back, a song you
added gets removed, your order gets re-sorted. The user wants the source's
changes to flow to the destination, while their own changes on the
destination are left alone.

## Goals

1. **Every transfer previews first.** The default flow is: dry run → review →
   "Start transfer". A "Transfer immediately" toggle skips the preview.
2. **Every new sync previews its first run.** The flow is: preview →
   review → "Start syncing", which creates the sync and runs it for real.
3. **Swap linked songs in the preview.** For any matched song, you can pick a
   different song on the destination service: one of the alternatives found,
   or one from a search. The real run uses your pick.
4. **Swaps stick.** A swap is remembered, so later transfers and syncs of the
   same source song to the same service use it too.
5. **Full syncs respect destination edits.** A full sync still mirrors the
   source's changes (songs removed from the source, edited playlist details,
   new songs). It no longer:
   - re-adds a song you deleted on the destination,
   - removes a song you added on the destination,
   - re-sorts a destination order you arranged yourself.
6. **Re-add.** For each song you deleted on a destination, the sync page has
   a button that puts it back. After that it's a normal synced song again.
7. **One-time reverse sync of your additions.** For songs that exist only on
   a destination, the sync page lets you copy them (all at once or one by
   one) into the source playlist. They then become ordinary source songs, so
   they also go to the sync's other destinations. This is a single push; it
   doesn't turn on ongoing two-way syncing.

## Non-goals

- Two-way syncing. Reverse sync is a manual one-off action only.
- Changing add-only syncs. They already keep destination deletions. They
  should get the re-add and reverse-sync actions too, but their planning
  doesn't change.
- Reworking the attention inbox. Unmatched songs keep working as they do;
  the preview reuses the same candidate and pick building blocks.

## Anti-goals (would make this a failure even if it works)

- The real run quietly links a different song than the preview showed,
  ignoring a swap.
- A full sync deletes a song the user added on the destination, or puts back
  one they deleted.
- Choosing "Transfer immediately" still makes you sit through a preview.

## Current state (for orientation)

- Transfers: `frontend/src/features/transfers/NewTransferPage.tsx` has a
  `dryRun` checkbox, off by default. Backend: `POST /api/transfers` and
  `/api/transfers/bulk` take `dry_run`. `TransferService.transfer` runs a
  throwaway one-way `SyncGroup` (`application/transfer.py`).
- New sync: `features/new-sync/NewSyncPage.tsx` already shows a live
  `PreviewPanel` for single syncs (via `POST /api/syncs/preview`). "Create"
  saves the sync and navigates away; bulk sync creation has no preview.
- Matching: `application/matching_service.py`. A `RememberedMatch` (via
  `MatchRepository`) wins over everything else and is reported as "your
  pick". The attention inbox already writes these (`api/attention.py`
  `pick`, `similar` → `application/suggestions.find_similar`).
  `Matched` carries no alternatives; only `NeedsAttention` carries
  candidates.
- Planning: `domain/planning/planner.py`. `_user_deleted` already detects
  destination deletions using `last_synced.entries`, but only in add-only
  mode. Full mode's `plan_member` removes everything not in `desired`
  (except `protected`) and reorders to the source's order.

## Decided behaviour

### Transfers

- The dry-run checkbox is replaced by a **"Transfer immediately"** toggle,
  off by default.
- Off: pressing the start button runs a dry run and shows the preview. This
  works for single and bulk transfers; bulk still shows the estimate/warning
  dialog first, as today. The preview shows, per playlist and destination:
  the songs it will add, the song it linked for each one, and what didn't
  match. A **"Start transfer"** button then does the real transfer with any
  swaps applied.
- On: behaves like today's non-dry-run transfer.

### New syncs

- The first run is always previewed. The existing single-sync preview panel
  gains swap controls. The create button becomes **"Start syncing"**, which
  saves the sync and immediately does a real first run that honours the
  swaps.
- Bulk sync creation gets the same preview → "Start syncing" flow.

### Swapping a match

- Every matched song in a preview has a "Change" action. It offers
  alternative songs found on the destination service, plus a search. Picking
  one replaces the link in the preview right away.
- Unmatched songs in the preview can be picked the same way.
- A swap is saved as a `RememberedMatch`, so it applies to the real run and
  every later run to that service.
- **Swaps reach existing syncs (user's decision).** If an existing sync
  already put the old version on that service, its next run swaps it for the
  picked version: add the new one, remove the old one, same position. This
  keeps everything consistent. The current matching order ("your pick" beats
  the previous sync's pairing) already does this. Even with the
  destination-edit rules below, the old version counts as sync-owned and is
  replaced, not kept as a "user addition".

### New sync onto an existing playlist with songs (user's decision)

When a new sync's destination is an existing playlist that already has songs,
the form shows a warning box with two choices:

- **Merge:** the existing songs count as the user's. They're kept, listed
  under "Added on <destination>", and can be copied to the source like any
  other addition. Source songs already in the playlist are paired with
  those copies, not duplicated.
- **Overwrite:** the first run makes the destination match the source once,
  removing songs that aren't in the source. From then on, normal rules apply.

The preview shows what the chosen option will do before "Start syncing".

### Full syncs and destination edits

The sync compares the destination against what it last wrote
(`last_synced`), and treats differences as the user's edits:

- **Deleted on the destination:** stays deleted, like add-only does today.
  Listed under "Removed on <destination>" with a **Re-add** button per song.
- **Added on the destination** (not from the source): kept, never removed.
  Listed under "Added on <destination>" with **"Copy to source"** per song
  and **"Copy all to source"**. Copying adds the songs to the source
  playlist; the next run sends them to the other destinations and pairs them
  with the existing copy on this destination (no duplicate).
- **Reordered on the destination (user's decision):** source order changes
  keep coming through, except for songs the user moved on the destination
  themselves; those stay where the user put them. If that turns out to make
  the planner much more complex, the accepted fallback is simpler: once a
  destination has been reordered by the user, source order changes stop
  reaching it.
- **Still mirrored from the source:** songs removed from the source are
  removed from the destination; edited playlist details are applied; new
  source songs are added.

## Calls made by me (not the user) — check these

- New source songs are added **at the end** of a destination whose order the
  user has arranged; I won't try to guess positions.
- A swap is saved **when you start the transfer/sync**, not on click, so
  abandoning a preview leaves nothing behind.
- The Removed/Added lists live on the **sync's page, grouped per
  destination**.
- Re-add and Copy-to-source are also offered on add-only syncs.
- **Existing full syncs switch to the new behaviour automatically.** Songs
  already on a destination that the sync didn't write (not in
  `last_synced`) count as the user's additions and are kept. One exception:
  candidate tracks the sync kept on purpose for unmatched songs stay
  sync-owned. This errs toward never deleting a user's song.
- **"Copy to source" edge cases:**
  - If the song can't be found on the source service, it's reported as not
    copied.
  - If the match is ambiguous, the same "Change" picker as the preview
    appears, so the right version (not a compilation) gets paired.
  - The button is hidden when the source playlist can't take songs (no ADD
    capability, or not owned).

## Left to the builder

- How a preview's match decisions get carried into the real run. You could
  re-match with remembered swaps or reuse the preview's decisions. Mind the
  API cost of searching twice, and make sure the real run matches the preview
  (see anti-goals).
- How "the user reordered" is detected (e.g. comparing against
  `last_synced` order), and the data model for tracking destination-only
  additions.
- API shapes, job types, and how the Removed/Added lists are computed
  (stored on runs vs computed live from a snapshot).
- Handling services without reorder/remove/add capabilities, in line with
  the existing "changes not made" notes.
