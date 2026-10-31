import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useId, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router";
import { api, call } from "@/api/client";
import { keys } from "@/api/keys";
import type { LibraryServiceOut, StatsOut } from "@/api/types";
import { ErrorNotice } from "@/components/ErrorNotice";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatDateTime } from "@/lib/format";
import { ActivityChart } from "./ActivityChart";

const PERIODS = [7, 30, 90] as const;
const DEFAULT_DAYS = 30;

const count = new Intl.NumberFormat();

function Section({ title, children }: { title: string; children: ReactNode }) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="space-y-3">
      <h2 id={id} className="text-lg font-semibold">
        {title}
      </h2>
      {children}
    </section>
  );
}

function Tiles({ items }: { items: [string, number][] }) {
  return (
    <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
      {items.map(([label, value]) => (
        <div key={label} className="rounded-md border p-3">
          <dt className="text-sm text-muted-foreground">{label}</dt>
          <dd className="text-2xl font-semibold">{count.format(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

function sum(rows: LibraryServiceOut[], field: "playlists" | "songs" | "archived" | "versions"): number {
  return rows.reduce((total, row) => total + row[field], 0);
}

function Library({ stats }: { stats: StatsOut }) {
  const rows = stats.library;
  if (rows.length === 0) {
    return (
      <Section title="Library">
        <p className="text-muted-foreground">
          No backups yet.{" "}
          <Link className="text-primary underline underline-offset-4" to="/backup">
            Back up now
          </Link>
        </p>
      </Section>
    );
  }
  return (
    <Section title="Library">
      <Tiles
        items={[
          ["Playlists", sum(rows, "playlists")],
          ["Songs", sum(rows, "songs")],
          ["Archived", sum(rows, "archived")],
          ["Versions kept", sum(rows, "versions")],
        ]}
      />
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Service</TableHead>
            <TableHead className="text-right">Playlists</TableHead>
            <TableHead className="text-right">Songs</TableHead>
            <TableHead className="text-right">Archived</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.service}>
              <TableCell>{row.display_name}</TableCell>
              <TableCell className="text-right tabular-nums">{count.format(row.playlists)}</TableCell>
              <TableCell className="text-right tabular-nums">{count.format(row.songs)}</TableCell>
              <TableCell className="text-right tabular-nums">{count.format(row.archived)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <p className="text-sm text-muted-foreground">
        From your backups
        {stats.last_backup_at ? `, last run ${formatDateTime(stats.last_backup_at)}` : ""}.
      </p>
    </Section>
  );
}

function Activity({ stats }: { stats: StatsOut }) {
  const a = stats.activity;
  const runs = a.sync_runs + a.transfer_runs + a.undo_runs;
  return (
    <Section title={`Activity in the last ${stats.days} days`}>
      {runs === 0 && a.backups === 0 ? (
        <p className="text-muted-foreground">No runs in this period</p>
      ) : (
        <>
          <Tiles
            items={[
              ["Sync runs", a.sync_runs],
              ["Transfers", a.transfer_runs],
              ["Undos", a.undo_runs],
              ["Songs added", a.songs_added],
              ["Songs removed", a.songs_removed],
              ["Backups", a.backups],
            ]}
          />
          <p className="text-sm">
            {`${a.succeeded} succeeded, ${a.partial} partly done, ${a.failed} failed, ${a.waiting} waiting, ${a.skipped} skipped`}
          </p>
          <p className="text-sm text-muted-foreground">Dry runs, and the changes undos make, aren't counted.</p>
          {runs > 0 ? <ActivityChart days={stats.daily} /> : null}
        </>
      )}
    </Section>
  );
}

function readDays(raw: string | null): number {
  const days = Number(raw);
  return PERIODS.some((p) => p === days) ? days : DEFAULT_DAYS;
}

export function StatsPage() {
  const [params, setParams] = useSearchParams();
  const days = readDays(params.get("days"));
  const stats = useQuery({
    queryKey: keys.stats(days),
    queryFn: () => call(api.GET("/api/stats", { params: { query: { days } } })),
    placeholderData: keepPreviousData,
  });
  const data = stats.data;
  return (
    <div className="space-y-8">
      <header className="space-y-3">
        <h1 className="text-2xl font-semibold">Stats</h1>
        <div role="group" aria-label="Period" className="flex gap-1">
          {PERIODS.map((p) => (
            <Button
              key={p}
              type="button"
              size="sm"
              variant={p === days ? "default" : "outline"}
              aria-pressed={p === days}
              onClick={() => setParams(p === DEFAULT_DAYS ? {} : { days: String(p) })}
            >
              {p} days
            </Button>
          ))}
        </div>
      </header>
      {stats.isError ? <ErrorNotice error={stats.error} onRetry={() => void stats.refetch()} /> : null}
      {data ? (
        <div className={`space-y-8 transition-opacity ${stats.isPlaceholderData ? "opacity-60" : ""}`}>
          <Library stats={data} />
          <Activity stats={data} />
          <Section title="Syncs">
            <Tiles
              items={[
                ["Syncs", data.syncs],
                ["Paused", data.paused_syncs],
              ]}
            />
          </Section>
          <Section title="Matching">
            <Tiles
              items={[
                ["Remembered matches", data.matching.remembered],
                ["Not on that service", data.matching.not_on_service],
                ["Needs attention", data.matching.open_attention],
              ]}
            />
            {data.matching.open_attention > 0 ? (
              <Link className="text-sm text-primary underline underline-offset-4" to="/">
                Review on Home
              </Link>
            ) : null}
          </Section>
        </div>
      ) : null}
    </div>
  );
}
