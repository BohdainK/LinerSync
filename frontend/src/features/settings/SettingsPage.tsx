import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState, type ReactNode } from "react";
import { toast } from "sonner";
import { api, call, fieldErrors, type ApiError } from "@/api/client";
import { keys } from "@/api/keys";
import type { SettingsIn, SettingsOut } from "@/api/types";
import { ErrorNotice } from "@/components/ErrorNotice";
import { ExternalLink } from "@/components/ExternalLink";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { DONATE_URL } from "@/config";

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

function SettingsForm({ settings }: { settings: SettingsOut }) {
  const queryClient = useQueryClient();
  const [v, setV] = useState({
    default_schedule: settings.default_schedule,
    percent: toPercent(settings.brake_max_fraction),
    count: String(settings.brake_max_count),
    accept: String(settings.match_accept_threshold),
    margin: String(settings.match_min_margin),
    failures: String(settings.breaker_failure_threshold),
    cooldown: String(settings.breaker_cooldown_minutes),
    backupOn: settings.backup_schedule !== null,
    backup_schedule: settings.backup_schedule ?? DEFAULT_BACKUP_SCHEDULE,
    kept: String(settings.backup_versions_kept),
  });
  const set = <K extends keyof typeof v>(k: K, value: (typeof v)[K]) => setV((c) => ({ ...c, [k]: value }));
  const save = useMutation<SettingsOut, ApiError, SettingsIn>({
    mutationFn: (body) => call(api.PUT("/api/settings", { body })),
    onSuccess: (s) => {
      queryClient.setQueryData(keys.settings, s);
      toast("Saved");
    },
  });
  const nums = [v.percent, v.count, v.accept, v.margin, v.failures, v.cooldown, v.kept].map((s) =>
    s.trim() === "" ? NaN : Number(s),
  );
  const invalid = nums.some(Number.isNaN);
  const errors = save.isError ? fieldErrors(save.error) : {};
  const known = [
    "default_schedule",
    "brake_max_fraction",
    "brake_max_count",
    "match_accept_threshold",
    "match_min_margin",
    "breaker_failure_threshold",
    "breaker_cooldown_minutes",
    "backup_schedule",
    "backup_versions_kept",
  ];
  const unmapped = save.isError && !known.some((k) => k in errors);

  function submit() {
    save.mutate({
      default_schedule: v.default_schedule.trim(),
      brake_max_fraction: Number(v.percent) / 100,
      brake_max_count: Number(v.count),
      match_accept_threshold: Number(v.accept),
      match_min_margin: Number(v.margin),
      breaker_failure_threshold: Number(v.failures),
      breaker_cooldown_minutes: Number(v.cooldown),
      backup_schedule: v.backupOn ? v.backup_schedule.trim() : null,
      backup_versions_kept: Number(v.kept),
    });
  }

  const num = (id: string, key: keyof typeof v, field: string, attrs: Record<string, number | string>) => (
    <Input
      id={id}
      type="number"
      className="max-w-40"
      value={String(v[key])}
      aria-invalid={errors[field] ? true : undefined}
      onChange={(e) => set(key, e.target.value as never)}
      {...attrs}
    />
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
      <Field label="Pause a run that removes more than (%)" error={errors["brake_max_fraction"]}>
        {(id) => num(id, "percent", "brake_max_fraction", { min: 1, max: 100, step: "any" })}
      </Field>
      <Field label="…or more than (songs)" error={errors["brake_max_count"]}>
        {(id) => num(id, "count", "brake_max_count", { min: 0, step: 1 })}
      </Field>
      <Field label="Accept a match from confidence" error={errors["match_accept_threshold"]}>
        {(id) => num(id, "accept", "match_accept_threshold", { min: 0, max: 1, step: 0.01 })}
      </Field>
      <Field label="Minimum lead over the next candidate" error={errors["match_min_margin"]}>
        {(id) => num(id, "margin", "match_min_margin", { min: 0, max: 1, step: 0.01 })}
      </Field>
      <Field label="Pause a service after (failures)" error={errors["breaker_failure_threshold"]}>
        {(id) => num(id, "failures", "breaker_failure_threshold", { min: 1, step: 1 })}
      </Field>
      <Field label="Retry a paused service after (minutes)" error={errors["breaker_cooldown_minutes"]}>
        {(id) => num(id, "cooldown", "breaker_cooldown_minutes", { min: 1, step: 1 })}
      </Field>
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
      <Field label="Versions kept per playlist" error={errors["backup_versions_kept"]}>
        {(id) => num(id, "kept", "backup_versions_kept", { min: 1, max: 100, step: 1 })}
      </Field>
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
  const settings = useQuery({
    queryKey: keys.settings,
    queryFn: () => call(api.GET("/api/settings")),
  });
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Settings</h1>
      {settings.isError ? <ErrorNotice error={settings.error} onRetry={() => void settings.refetch()} /> : null}
      {settings.data ? <SettingsForm settings={settings.data} /> : null}
      {DONATE_URL ? (
        <footer className="text-sm">
          <ExternalLink href={DONATE_URL}>Support LinerSync</ExternalLink>
        </footer>
      ) : null}
    </div>
  );
}
