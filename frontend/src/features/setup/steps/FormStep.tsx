import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState, type FormEvent } from "react";
import { toast } from "sonner";
import { api, call, fieldErrors } from "@/api/client";
import { keys } from "@/api/keys";
import type { ConnectionOut, SetupField, SetupStep } from "@/api/types";
import { ErrorNotice } from "@/components/ErrorNotice";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { SECRET_SAVED } from "@/copy";
import { checkField } from "../checkField";
import { StepBody } from "./InfoStep";

/** A stored secret is kept when its input is left blank (the server's rule). */
function isSavedSecret(field: SetupField, conn: ConnectionOut): boolean {
  return field.kind !== "text" && conn.secrets_set.includes(field.key);
}

/** Text fields keep their value from `from`; secrets start blank. */
function blankSecrets(fields: SetupField[], from: Record<string, string>): Record<string, string> {
  return Object.fromEntries(fields.map((f) => [f.key, f.kind === "text" ? (from[f.key] ?? "") : ""]));
}

function FieldInput({
  field,
  value,
  saved,
  error,
  onChange,
  onBlur,
}: {
  field: SetupField;
  value: string;
  saved: boolean;
  error: string | null;
  onChange: (v: string) => void;
  onBlur: () => void;
}) {
  const id = useId();
  const helpId = `${id}-help`;
  const errorId = `${id}-error`;
  const describedBy = [field.help ? helpId : null, error ? errorId : null].filter(Boolean).join(" ") || undefined;
  const common = {
    id,
    name: field.key,
    value,
    placeholder: saved ? SECRET_SAVED : field.placeholder,
    "aria-describedby": describedBy,
    "aria-invalid": error ? true : undefined,
    onBlur,
  };
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>{field.label}</Label>
      {field.kind === "multiline_secret" ? (
        <Textarea {...common} rows={5} className="font-mono" onChange={(e) => onChange(e.target.value)} />
      ) : (
        <Input
          {...common}
          type={field.kind === "secret" ? "password" : "text"}
          autoComplete={field.kind === "secret" ? "new-password" : "off"}
          onChange={(e) => onChange(e.target.value)}
        />
      )}
      {field.help ? (
        <p id={helpId} className="text-sm text-muted-foreground">
          {field.help}
        </p>
      ) : null}
      {error ? (
        <p id={errorId} className="text-sm text-destructive">
          {error}
        </p>
      ) : null}
    </div>
  );
}

export function FormStep({
  service,
  step,
  conn,
  onSaved,
}: {
  service: string;
  step: SetupStep;
  conn: ConnectionOut;
  onSaved: () => void;
}) {
  const queryClient = useQueryClient();
  const [values, setValues] = useState<Record<string, string>>(() => blankSecrets(step.fields, conn.values));
  const [touched, setTouched] = useState<Set<string>>(() => new Set());
  const [serverErrors, setServerErrors] = useState<Record<string, string>>({});

  const save = useMutation({
    mutationFn: (body: Record<string, string>) =>
      call(
        api.PUT("/api/connections/{service}/credentials", {
          params: { path: { service } },
          body: { values: body },
        }),
      ),
    onSuccess: (saved) => {
      queryClient.setQueryData(keys.connection(service), saved);
      void queryClient.invalidateQueries({ queryKey: keys.connections, exact: true });
      setValues((v) => blankSecrets(step.fields, v));
      toast.success("Saved");
      onSaved();
    },
    onError: (err) => setServerErrors(fieldErrors(err)),
  });

  const clientError = (f: SetupField) => checkField(f, values[f.key] ?? "", isSavedSecret(f, conn));
  const shownError = (f: SetupField) =>
    (touched.has(f.key) ? clientError(f) : null) ?? serverErrors[f.key] ?? null;
  const touch = (key: string) => setTouched((t) => (t.has(key) ? t : new Set(t).add(key)));

  const submit = (e: FormEvent) => {
    e.preventDefault();
    setTouched(new Set(step.fields.map((f) => f.key)));
    if (step.fields.some((f) => clientError(f) !== null)) return;
    setServerErrors({});
    save.mutate(Object.fromEntries(step.fields.map((f) => [f.key, (values[f.key] ?? "").trim()])));
  };

  const generalError = save.isError && !step.fields.some((f) => serverErrors[f.key]);

  return (
    <div className="flex flex-col gap-3">
      <StepBody service={service} step={step} />
      <form noValidate onSubmit={submit} className="flex max-w-xl flex-col gap-4">
        {step.fields.map((f) => (
          <FieldInput
            key={f.key}
            field={f}
            value={values[f.key] ?? ""}
            saved={isSavedSecret(f, conn)}
            error={shownError(f)}
            onChange={(v) => {
              setValues((prev) => ({ ...prev, [f.key]: v }));
              touch(f.key);
              setServerErrors((prev) => {
                if (!(f.key in prev)) return prev;
                const next = { ...prev };
                delete next[f.key];
                return next;
              });
            }}
            onBlur={() => touch(f.key)}
          />
        ))}
        {generalError ? <ErrorNotice error={save.error} /> : null}
        <div>
          <Button type="submit" disabled={save.isPending}>
            {save.isPending ? "Saving…" : "Save"}
          </Button>
        </div>
      </form>
    </div>
  );
}
