import { useId } from "react";
import { toast } from "sonner";
import type { CopyValue, Link, SetupStep } from "@/api/types";
import { ExternalLink } from "@/components/ExternalLink";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Screenshot } from "./Screenshot";

/** Untrusted guide text: plain paragraphs, split on blank lines. */
export function Paragraphs({ text }: { text: string }) {
  const parts = text
    .split(/\n\s*\n/)
    .map((p) => p.trim())
    .filter(Boolean);
  return (
    <>
      {parts.map((p, i) => (
        <p key={i} className="whitespace-pre-line">
          {p}
        </p>
      ))}
    </>
  );
}

export function LinkList({ links }: { links: Link[] }) {
  if (links.length === 0) return null;
  return (
    <ul className="list-disc pl-5">
      {links.map((l, i) => (
        <li key={i}>
          <ExternalLink href={l.url}>{l.label}</ExternalLink>
        </li>
      ))}
    </ul>
  );
}

function CopyField({ item }: { item: CopyValue }) {
  const id = useId();
  const copy = () => {
    navigator.clipboard.writeText(item.value).then(
      () => toast.success(`Copied ${item.label}`),
      () => toast.error("Couldn't copy. Select the text and copy it yourself."),
    );
  };
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>{item.label}</Label>
      <div className="flex gap-2">
        <Input id={id} readOnly value={item.value} className="font-mono" onFocus={(e) => e.currentTarget.select()} />
        <Button type="button" variant="outline" onClick={copy}>
          Copy {item.label}
        </Button>
      </div>
    </div>
  );
}

/** The parts every step kind can carry: text, links, copy values and screenshots. */
export function StepBody({ service, step }: { service: string; step: SetupStep }) {
  return (
    <div className="flex flex-col gap-3 text-sm">
      <Paragraphs text={step.body} />
      <LinkList links={step.links} />
      {step.copy_values.map((c, i) => (
        <CopyField key={i} item={c} />
      ))}
      {step.screenshots.map((s, i) => (
        <Screenshot key={i} service={service} shot={s} />
      ))}
    </div>
  );
}

export function InfoStep({
  service,
  step,
  onAcknowledge,
}: {
  service: string;
  step: SetupStep;
  onAcknowledge: () => void;
}) {
  return (
    <div className="flex flex-col gap-3">
      <StepBody service={service} step={step} />
      <div>
        <Button type="button" onClick={onAcknowledge}>
          I've done this
        </Button>
      </div>
    </div>
  );
}
