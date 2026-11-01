import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { postFile, type ApiError } from "@/api/client";
import type { RestoreReportOut } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { ErrorNotice } from "@/components/ErrorNotice";
import { Section } from "@/components/Section";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { MAX_IMPORT_BYTES } from "@/config";
import { IMPORT_TOO_LARGE } from "@/copy";

function ImportReport({ report }: { report: RestoreReportOut }) {
  const kinds = [...new Set([...Object.keys(report.added), ...Object.keys(report.skipped)])].sort();
  return (
    <div className="space-y-2">
      <Table aria-label="Import report">
        <TableHeader>
          <TableRow>
            <TableHead>Kind</TableHead>
            <TableHead>Added</TableHead>
            <TableHead>Skipped</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {kinds.map((k) => (
            <TableRow key={k}>
              <TableCell>{k}</TableCell>
              <TableCell>{report.added[k] ?? 0}</TableCell>
              <TableCell>{report.skipped[k] ?? 0}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <p className="text-sm">{report.settings_applied ? "Settings applied" : "Your settings were kept"}</p>
    </div>
  );
}

export function ImportExport() {
  const queryClient = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [tooLarge, setTooLarge] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [report, setReport] = useState<RestoreReportOut | null>(null);
  const upload = useMutation<RestoreReportOut, ApiError, File>({
    mutationFn: (f) => postFile("/api/import", f),
    onSuccess: (r) => {
      setReport(r);
      void queryClient.invalidateQueries();
    },
  });

  function pick(f: File | undefined) {
    setReport(null);
    upload.reset();
    setTooLarge(false);
    if (input.current) input.current.value = "";
    if (!f) return;
    if (f.size > MAX_IMPORT_BYTES) setTooLarge(true);
    else setFile(f);
  }

  return (
    <Section title="Export and import">
      <div className="flex flex-wrap items-center gap-3">
        <Button asChild variant="outline" size="sm">
          <a href="/api/export" download>
            Download JSON export
          </a>
        </Button>
        <span className="text-sm text-muted-foreground">
          Holds your library data unencrypted. Store it like the data folder.
        </span>
      </div>
      <div className="flex flex-col gap-1">
        <label htmlFor="import-file" className="text-sm font-medium">
          Import an export…
        </label>
        <input
          id="import-file"
          ref={input}
          type="file"
          accept="application/json,.json"
          disabled={upload.isPending}
          className="text-sm"
          onChange={(e) => pick(e.target.files?.[0])}
        />
      </div>
      {tooLarge ? (
        <p role="alert" className="text-sm text-destructive">
          {IMPORT_TOO_LARGE}
        </p>
      ) : null}
      {upload.isError ? <ErrorNotice error={upload.error} /> : null}
      {report ? <ImportReport report={report} /> : null}
      <ConfirmDialog
        open={file !== null}
        title={`Import ${file?.name ?? ""}?`}
        body="Your local data wins: anything that already exists is skipped. Imported syncs are paused until you resume them."
        confirmLabel="Import"
        pending={upload.isPending}
        onOpenChange={(o) => {
          if (!o) setFile(null);
        }}
        onConfirm={() => {
          if (file) upload.mutate(file);
          setFile(null);
        }}
      />
    </Section>
  );
}
