import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState, type ReactNode } from "react";
import { toast } from "sonner";
import { api, call, fieldErrors, type ApiError } from "@/api/client";
import { keys } from "@/api/keys";
import type { SettingsIn, SettingsOut } from "@/api/types";
import { ErrorNotice, QueryError } from "@/components/ErrorNotice";
import { ExternalLink } from "@/components/ExternalLink";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { DONATE_URL } from "@/config";
import { useSettings } from "./useSettings";

const DEFAULT_BACKUP_SCHEDULE = "0 3 * * *";

function Field(props: { label: string; error?: string | undefined; children: (id: string) => ReactNode }) {
  const id = useId();
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="text-sm font-medium">
        {props.label}
      </label>
      {props.children(id)}
      {props.error ? <p className="text-sm text-destructive">{props.error}</p> : null}
    </div>
  );
}

// 0.2 * 100 is 20.000000000000004; round to two decimals for display.
const toPercent = (f: number) => String(Math.round(f * 10_000) / 100);

type NumberField = {
  key:
    | "brake_max_fraction"
    | "brake_max_count"
    | "match_accept_threshold"
    | "match_min_margin"
    | "breaker_failure_threshold"
    | "breaker_cooldown_minutes"
    | "backup_versions_kept";
  label: string;
  attrs: { min: number; max?: number; step: number | "any" };
  /** Shown as a percentage, stored as a fraction. */
  percent?: boolean;
};

/** The numeric settings shown before the backup schedule, in order. */
const LIMIT_FIELDS: NumberField[] = [
  {
    key: "brake_max_fraction",
    label: "Pause a run that removes more than (%)",
    attrs: { min: 1, max: 100, step: "any" },
    percent: true,
  },
  { key: "brake_max_count", label: "…or more than (songs)", attrs: { min: 0, step: 1 } },
  { key: "match_accept_threshold", label: "Accept a match from confidence", attrs: { min: 0, max: 1, step: 0.01 } },
  { key: "match_min_margin", label: "Minimum lead over the next candidate", attrs: { min: 0, max: 1, step: 0.01 } },
  { key: "breaker_failure_threshold", label: "Pause a service after (failures)", attrs: { min: 1, step: 1 } },
  { key: "breaker_cooldown_minutes", label: "Retry a paused service after (minutes)", attrs: { min: 1, step: 1 } },
];
const KEPT_FIELD: NumberField = {
  key: "backup_versions_kept",
  label: "Versions kept per playlist",
  attrs: { min: 1, max: 100, step: 1 },
};
const NUMBER_FIELDS = [...LIMIT_FIELDS, KEPT_FIELD];

type Numbers = Record<NumberField["key"], string>;

function SettingsForm({ settings }: { settings: SettingsOut }) {
  const queryClient = useQueryClient();
  const [v, setV] = useState({
    default_schedule: settings.default_schedule,
    backupOn: settings.backup_schedule !== null,
    backup_schedule: settings.backup_schedule ?? DEFAULT_BACKUP_SCHEDULE,
  });
  const [numbers, setNumbers] = useState(
    () =>
      Object.fromEntries(
        NUMBER_FIELDS.map((f) => [f.key, f.percent ? toPercent(settings[f.key]) : String(settings[f.key])]),
      ) as Numbers,
  );
  const set = <K extends keyof typeof v>(k: K, value: (typeof v)[K]) => setV((c) => ({ ...c, [k]: value }));
  const save = useMutation<SettingsOut, ApiError, SettingsIn>({
    mutationFn: (body) => call(api.PUT("/api/settings", { body })),
    onSuccess: (s) => {
      queryClient.setQueryData(keys.settings, s);
      toast("Saved");
    },
  });
  const invalid = NUMBER_FIELDS.some((f) => numbers[f.key].trim() === "" || Number.isNaN(Number(numbers[f.key])));
  const errors = save.isError ? fieldErrors(save.error) : {};
  const known = ["default_schedule", "backup_schedule", ...NUMBER_FIELDS.map((f) => f.key)];
  const unmapped = save.isError && !known.some((k) => k in errors);

  function submit() {
    const parsed = Object.fromEntries(
      NUMBER_FIELDS.map((f) => [f.key, Number(numbers[f.key]) / (f.percent ? 100 : 1)]),
    ) as Record<NumberField["key"], number>;
    save.mutate({
      ...parsed,
      default_schedule: v.default_schedule.trim(),
      backup_schedule: v.backupOn ? v.backup_schedule.trim() : null,
    });
  }

  const numberField = (f: NumberField) => (
    <Field key={f.key} label={f.label} error={errors[f.key]}>
      {(id) => (
        <Input
          id={id}
          type="number"
          className="max-w-40"
          value={numbers[f.key]}
          aria-invalid={errors[f.key] ? true : undefined}
          onChange={(e) => setNumbers((n) => ({ ...n, [f.key]: e.target.value }))}
          {...f.attrs}
        />
      )}
    </Field>
  );

  return (
    <form
      className="max-w-xl space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <Field label="Default sync schedule" error={errors["default_schedule"]}>
        {(id) => (
          <Input
            id={id}
            className="max-w-xs font-mono"
            value={v.default_schedule}
            aria-invalid={errors["default_schedule"] ? true : undefined}
            onChange={(e) => set("default_schedule", e.target.value)}
          />
        )}
      </Field>
      {LIMIT_FIELDS.map(numberField)}
      <div className="flex items-center gap-2">
        <Checkbox
          id="backup-on"
          checked={v.backupOn}
          onCheckedChange={(c) => set("backupOn", c === true)}
        />
        <label htmlFor="backup-on" className="text-sm font-medium">
          Scheduled backups
        </label>
      </div>
      <Field label="Backup schedule" error={errors["backup_schedule"]}>
        {(id) => (
          <Input
            id={id}
            className="max-w-xs font-mono"
            disabled={!v.backupOn}
            value={v.backup_schedule}
            aria-invalid={errors["backup_schedule"] ? true : undefined}
            onChange={(e) => set("backup_schedule", e.target.value)}
          />
        )}
      </Field>
      {numberField(KEPT_FIELD)}
      <p className="text-sm text-muted-foreground">
        Schedules use {settings.timezone}. Change it with LINERSYNC_TIMEZONE.
      </p>
      {unmapped ? <ErrorNotice error={save.error} /> : null}
      <Button type="submit" disabled={save.isPending || invalid}>
        Save
      </Button>
    </form>
  );
}

export function SettingsPage() {
  const settings = useSettings();
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Settings</h1>
      <QueryError query={settings} />
      {settings.data ? <SettingsForm settings={settings.data} /> : null}
      {DONATE_URL ? (
        <footer className="text-sm">
          <ExternalLink href={DONATE_URL}>Support LinerSync</ExternalLink>
        </footer>
      ) : null}
    </div>
  );
}
