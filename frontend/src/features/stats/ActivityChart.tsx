import { useState } from "react";
import type { DayOut } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatDay } from "@/lib/format";
import { niceTicks } from "./ticks";

type Outcome = "succeeded" | "partial" | "failed";

// Status colors (good, warning, critical), bottom of the stack first. The
// warning step is light on white, so the legend and the table carry the values.
const OUTCOMES: { key: Outcome; label: string; color: string }[] = [
  { key: "succeeded", label: "Succeeded", color: "#0ca30c" },
  { key: "partial", label: "Partly done or waiting", color: "#fab219" },
  { key: "failed", label: "Failed", color: "#d03b3b" },
];

function total(d: DayOut): number {
  return d.succeeded + d.partial + d.failed;
}

function Swatch({ color }: { color: string }) {
  return <span aria-hidden className="inline-block size-2.5 shrink-0 rounded-sm" style={{ backgroundColor: color }} />;
}

function Column({ day, top, onHover }: { day: DayOut; top: number; onHover: (day: string | null) => void }) {
  const filled = OUTCOMES.filter((o) => day[o.key] > 0);
  const last = filled.at(-1)?.key;
  return (
    <div
      data-day={day.day}
      className="group h-full min-w-0 flex-1"
      onPointerEnter={() => onHover(day.day)}
      onPointerLeave={() => onHover(null)}
    >
      <div className="mx-auto flex h-full w-full max-w-6 flex-col-reverse gap-[2px] group-hover:opacity-80">
        {filled.map((o) => (
          <div
            key={o.key}
            className={o.key === last ? "rounded-t" : undefined}
            style={{ height: `${(day[o.key] / top) * 100}%`, backgroundColor: o.color }}
          />
        ))}
      </div>
    </div>
  );
}

function Tooltip({ day, index, count }: { day: DayOut; index: number; count: number }) {
  const left = ((index + 0.5) / count) * 100;
  return (
    <div
      role="tooltip"
      className="pointer-events-none absolute top-0 z-10 -translate-x-1/2 rounded-md border bg-background px-3 py-2 text-sm shadow-sm"
      style={{ left: `clamp(4rem, ${left}%, calc(100% - 4rem))` }}
    >
      <p className="mb-1 text-muted-foreground">{formatDay(day.day)}</p>
      {[...OUTCOMES].reverse().map((o) => (
        <p key={o.key} className="flex items-center gap-2 whitespace-nowrap">
          <span aria-hidden className="inline-block h-0.5 w-3" style={{ backgroundColor: o.color }} />
          <strong className="font-semibold tabular-nums">{day[o.key]}</strong>
          <span className="text-muted-foreground">{o.label}</span>
        </p>
      ))}
    </div>
  );
}

function DaysTable({ days }: { days: DayOut[] }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Day</TableHead>
          {OUTCOMES.map((o) => (
            <TableHead key={o.key} className="text-right">
              {o.label}
            </TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {days.map((d) => (
          <TableRow key={d.day}>
            <TableCell>{formatDay(d.day)}</TableCell>
            {OUTCOMES.map((o) => (
              <TableCell key={o.key} className="text-right tabular-nums">
                {d[o.key]}
              </TableCell>
            ))}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

export function ActivityChart({ days }: { days: DayOut[] }) {
  const [hovered, setHovered] = useState<string | null>(null);
  const [asTable, setAsTable] = useState(false);
  const ticks = niceTicks(Math.max(0, ...days.map(total)));
  const top = ticks.at(-1) ?? 1;
  const index = days.findIndex((d) => d.day === hovered);
  const first = days.at(0);
  const lastDay = days.at(-1);
  return (
    <figure className="space-y-3">
      <figcaption className="text-sm font-medium">Runs per day</figcaption>
      <ul aria-label="Legend" className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
        {OUTCOMES.map((o) => (
          <li key={o.key} className="flex items-center gap-1.5">
            <Swatch color={o.color} />
            <span className="text-muted-foreground">{o.label}</span>
            <span className="font-medium">{days.reduce((n, d) => n + d[o.key], 0)}</span>
          </li>
        ))}
      </ul>
      <div aria-hidden className="flex gap-2">
        <div className="relative h-40 w-8 shrink-0 text-right text-xs text-muted-foreground tabular-nums">
          {ticks.map((t) => (
            <span key={t} className="absolute right-0 translate-y-1/2" style={{ bottom: `${(t / top) * 100}%` }}>
              {t}
            </span>
          ))}
        </div>
        <div className="relative min-w-0 flex-1">
          <div className="relative h-40">
            {ticks.map((t) => (
              <div
                key={t}
                className={`absolute inset-x-0 h-px ${t === 0 ? "bg-border" : "bg-border/50"}`}
                style={{ bottom: `${(t / top) * 100}%` }}
              />
            ))}
            <div className="relative flex h-full gap-[2px]">
              {days.map((d) => (
                <Column key={d.day} day={d} top={top} onHover={setHovered} />
              ))}
            </div>
            {index >= 0 && days[index] ? <Tooltip day={days[index]} index={index} count={days.length} /> : null}
          </div>
          <div className="mt-1 flex justify-between text-xs text-muted-foreground">
            <span>{first ? formatDay(first.day) : ""}</span>
            <span>{lastDay && lastDay !== first ? formatDay(lastDay.day) : ""}</span>
          </div>
        </div>
      </div>
      <Button type="button" variant="link" size="sm" className="px-0" onClick={() => setAsTable((v) => !v)}>
        {asTable ? "Hide table" : "Show as table"}
      </Button>
      {asTable ? <DaysTable days={days} /> : null}
    </figure>
  );
}
