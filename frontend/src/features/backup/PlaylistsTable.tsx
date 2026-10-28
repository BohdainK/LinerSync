import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { api, call } from "@/api/client";
import { keys } from "@/api/keys";
import type { BackupServiceOut } from "@/api/types";
import { ErrorNotice } from "@/components/ErrorNotice";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { selectClass } from "@/features/pickers/ServiceSelect";
import { formatDateTime } from "@/lib/format";

export function PlaylistsTable({ services }: { services: BackupServiceOut[] }) {
  const [tab, setTab] = useState<"live" | "archive">("live");
  const [service, setService] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const archived = tab === "archive";
  const playlists = useQuery({
    queryKey: keys.backupPlaylists({ ...(service ? { service } : {}), archived }),
    queryFn: () =>
      call(
        api.GET("/api/backups/playlists", {
          params: { query: { ...(service ? { service } : {}), archived } },
        }),
      ),
  });
  const nameOf = (id: string) => services.find((s) => s.service === id)?.display_name ?? id;
  const rows = playlists.data ?? [];
  const chosen = rows.filter((r) => selected.includes(r.entry_id));
  const csvHref = "/api/export/csv?" + chosen.map((r) => `entry_id=${encodeURIComponent(r.entry_id)}`).join("&");

  function toggle(id: string, on: boolean) {
    setSelected((cur) => (on ? [...cur, id] : cur.filter((x) => x !== id)));
  }

  return (
    <section aria-labelledby="playlists" className="space-y-3">
      <h2 id="playlists" className="text-lg font-semibold">
        Playlists
      </h2>
      <Tabs
        value={tab}
        onValueChange={(v) => {
          setTab(v === "archive" ? "archive" : "live");
          setSelected([]);
        }}
      >
        <TabsList>
          <TabsTrigger value="live">Backed up</TabsTrigger>
          <TabsTrigger value="archive">Archive</TabsTrigger>
        </TabsList>
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex flex-col gap-1">
            <label htmlFor="backup-service-filter" className="text-sm font-medium">
              Service
            </label>
            <select
              id="backup-service-filter"
              className={selectClass}
              value={service}
              onChange={(e) => {
                setService(e.target.value);
                setSelected([]);
              }}
            >
              <option value="">All services</option>
              {services.map((s) => (
                <option key={s.service} value={s.service}>
                  {s.display_name}
                </option>
              ))}
            </select>
          </div>
          {chosen.length > 0 ? (
            <Button asChild variant="outline" size="sm">
              <a href={csvHref} download>
                Download CSV
              </a>
            </Button>
          ) : (
            <Button type="button" variant="outline" size="sm" disabled>
              Download CSV
            </Button>
          )}
          <Button asChild variant="outline" size="sm">
            <a href="/api/export/csv" download>
              Download all as CSV
            </a>
          </Button>
        </div>
        <TabsContent value={tab}>
          {playlists.isError ? (
            <ErrorNotice error={playlists.error} onRetry={() => void playlists.refetch()} />
          ) : null}
          {playlists.data && rows.length === 0 ? (
            <p className="text-muted-foreground">{archived ? "Nothing archived yet" : "Nothing backed up yet"}</p>
          ) : null}
          {rows.length > 0 ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>
                    <span className="sr-only">Select</span>
                  </TableHead>
                  <TableHead>
                    <span className="sr-only">Cover</span>
                  </TableHead>
                  <TableHead>Name</TableHead>
                  <TableHead>Service</TableHead>
                  <TableHead>Songs</TableHead>
                  <TableHead>Versions</TableHead>
                  <TableHead>Last seen</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((p) => (
                  <TableRow key={p.entry_id}>
                    <TableCell>
                      <Checkbox
                        aria-label={`Select ${p.name}`}
                        checked={selected.includes(p.entry_id)}
                        onCheckedChange={(c) => toggle(p.entry_id, c === true)}
                      />
                    </TableCell>
                    <TableCell>
                      {p.cover_url ? (
                        <img src={p.cover_url} alt="" width={40} height={40} />
                      ) : (
                        <span aria-hidden className="inline-block size-10 rounded bg-muted" />
                      )}
                    </TableCell>
                    <TableCell className="space-x-2">
                      <Link
                        className="text-primary underline underline-offset-4"
                        to={`/backup/playlists/${p.entry_id}`}
                      >
                        {p.name}
                      </Link>
                      {p.missing_since ? (
                        <Badge variant="outline">Missing since {formatDateTime(p.missing_since)}</Badge>
                      ) : null}
                    </TableCell>
                    <TableCell>{nameOf(p.service)}</TableCell>
                    <TableCell>{p.track_count}</TableCell>
                    <TableCell>{p.versions}</TableCell>
                    <TableCell>{formatDateTime(p.last_seen_at)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : null}
        </TabsContent>
      </Tabs>
    </section>
  );
}
