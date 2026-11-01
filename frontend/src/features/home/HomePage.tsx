import { useQuery } from "@tanstack/react-query";
import { api, call } from "@/api/client";
import { keys } from "@/api/keys";
import { QueryError } from "@/components/ErrorNotice";
import { ApiLimits } from "./panels/ApiLimits";
import { NeedsAttention } from "./panels/NeedsAttention";
import { NextUp } from "./panels/NextUp";
import { RecentActivity } from "./panels/RecentActivity";
import { RunningNow } from "./panels/RunningNow";

export function HomePage() {
  const home = useQuery({ queryKey: keys.home, queryFn: () => call(api.GET("/api/home")) });
  return (
    <div className="space-y-8">
      <h1 className="text-2xl font-semibold">Home</h1>
      <QueryError query={home} />
      {home.data ? (
        <>
          <RunningNow running={home.data.running} queued={home.data.queued} />
          <NeedsAttention warnings={home.data.warnings} />
          <NextUp items={home.data.next_up} />
          <RecentActivity jobs={home.data.recent} />
          <ApiLimits limits={home.data.api_limits} />
        </>
      ) : null}
    </div>
  );
}
