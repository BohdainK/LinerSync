# Stats page design

Written solo on 2026-10-31 while the owner was away. Every decision below is the
builder's call unless marked otherwise; the open questions are at the end.

## Intent

The main spec lists a Stats page under "Later", and the app has shipped a placeholder
("Stats are coming in a later version.") with a "Later" badge in the sidebar. This
replaces the placeholder with a first, useful version: a read-only overview of what
LinerSync holds and what it has done, built only from data the app already stores.

## Goals

- One page that answers: how big is my library across services, how much has LinerSync
  been doing lately, how is it going (successes vs failures), and how much has matching
  learned.
- A time window for activity: last 7, 30 or 90 days (default 30). The window is whole
  days in the server's timezone (`LINERSYNC_TIMEZONE`), today included, so the daily
  chart and the totals cover exactly the same runs.
- No new tables, no migration, no new background work.

## Non-goals

- Listening statistics (plays, top artists): LinerSync doesn't read listening history.
- A chart library. The one chart is plain HTML/CSS.
- Per-sync drill-down pages. The sync's own page already lists its runs.

## Anti-goals

- Stats that are slow enough to make the page hang on a big library.
- Numbers that look precise but aren't: dry runs and previews never count as changes.

## What the page shows

API: `GET /api/stats?days=N` (signed-in only, `N` in 1..365, default 30). One response:

1. **Library** (from the backup store, so it reflects the last backup, not a live read):
   per set-up-or-backed-up service: playlists (not archived), songs (sum of their track
   counts), archived playlists. Plus totals and the total number of stored versions.
   Services are listed by display name; a service with backups but no longer connected
   still shows (its data is still in the backup).
2. **Syncs**: how many syncs exist, and how many are paused.
3. **Activity in the window** (runs whose `started_at` is in the window, dry runs left
   out):
   - runs by kind: sync, transfer, undo;
   - runs by outcome: succeeded, partial, failed (failed + aborted), waiting
     (awaiting approval + paused because the source is missing), skipped; running runs
     are left out;
   - songs added and songs removed: summed from the operations each member actually
     applied (`applied_op_ids` matched against the plan's `AddTracks` / `RemoveTracks`).
     Undo runs carry no plan, so their changes aren't counted. Nor are the changes of
     a re-plan after a failed write: only the first plan is stored with the run. The
     page says "Dry runs, and the changes undos make, aren't counted.";
   - backups: number of backup runs in the window and the time of the last backup run
     (any time).
   - per day: runs that succeeded, were partly done or are waiting (partial, awaiting
     approval, paused because the source is missing), and failed (failed, aborted).
     Skipped runs aren't in the chart.
4. **Matching**: remembered matches total, of which "not on that service" (remembered
   with no target), and the current number of open "Needs attention" items, with a
   "Review on Home" link when there are any (the items are handled in Home's Needs
   attention panel).

**Runs per day chart** (under the activity tiles, only when the period has runs): a
stacked column per day, bottom to top succeeded / partly done or waiting / failed, in
the status colors good `#0ca30c`, warning `#fab219` and critical `#d03b3b`. Columns
are at most 24px wide with a 2px surface gap, a rounded top, a 1/2/5-step y axis with
at most five ticks, and the first and last day under the x axis. A legend shows each
outcome's total. Hovering a column shows that day's numbers. The chart is hidden from
screen readers; "Show as table" gives the same numbers as a table. The warning yellow
is below 3:1 contrast on white, which the legend and table make up for. While another
period loads, the previous numbers stay on screen, dimmed.

Frontend: replace `StatsPage` with a page in `src/features/stats/`. Heading "Stats", a
period picker (three buttons: 7 / 30 / 90 days, kept in the address as `?days=`), then sections "Library", "Activity", "Matching", each a
row of number tiles plus, for Library, a per-service table. Empty states: "No backups
yet" (with a link to Backup) when the library is empty, "No runs in this period" when the
activity counts are all zero. Remove the "Later" badge from the sidebar and the
`STATS_LATER` copy.

## How it's built (builder's calls)

- `RunRepository.list_since(since)`: runs with `started_at >= since`, oldest first. Both
  the SQLite and the in-memory repository, covered by the repository contract tests.
  SQLite filters on the `started_at` column, so old runs' bodies aren't decoded.
  `started_at` is stored as ISO text in UTC, so a text comparison is correct.
- The counting lives in `application/stats.py` as a pure function over runs, backup
  entries, syncs, matches and the open-attention count, so it is unit-tested without the
  server. The API module only gathers inputs and maps to schemas.
- `days` outside 1..365 is a 422 (FastAPI validation).
- OpenAPI file and `schema.d.ts` regenerated.

## Owner's answers (2026-10-31)

1. Library from the backup store: fine.
2. Per-day activity chart: build it (done, see above).
3. Per-sync stats: not needed.
4. "Needs attention" should link to where the items are handled (done).
