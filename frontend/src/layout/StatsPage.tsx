import { STATS_LATER } from "@/copy";

export function StatsPage() {
  return (
    <div className="flex flex-col gap-2">
      <h1 className="text-2xl font-semibold">Stats</h1>
      <p>{STATS_LATER}</p>
    </div>
  );
}
