import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { Navigate, Outlet, useLocation, useNavigate } from "react-router";
import { setUnauthorizedHandler } from "@/api/client";
import { EventsProvider } from "@/live/events";
import { isSetUp, useConnections } from "@/features/connections/useConnections";
import { ONBOARDING_SKIPPED_KEY, readFlag } from "@/lib/storage";
import { Toaster } from "@/components/ui/sonner";
import { Sidebar } from "./Sidebar";

export function AppShell() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();
  const here = useRef(location);
  useEffect(() => {
    here.current = location;
  }, [location]);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      const { pathname, search } = here.current;
      if (pathname === "/login") return;
      queryClient.clear();
      navigate(`/login?next=${encodeURIComponent(pathname + search)}&ended=1`, { replace: true });
    });
    return () => setUnauthorizedHandler(null);
  }, [queryClient, navigate]);

  // D5: only the exact path "/" redirects, so deep links keep working.
  const connections = useConnections();
  const nothingSetUp =
    connections.data !== undefined &&
    connections.data.services.length > 0 &&
    connections.data.services.every((c) => !isSetUp(c));
  if (location.pathname === "/" && nothingSetUp && readFlag(ONBOARDING_SKIPPED_KEY) !== "1") {
    return <Navigate to="/onboarding" replace />;
  }

  return (
    <EventsProvider>
      <div className="flex min-h-screen">
        <aside className="w-64 shrink-0 border-r">
          <Sidebar />
        </aside>
        <main className="min-w-0 flex-1 p-6">
          <div className="mx-auto max-w-6xl">
            <Outlet />
          </div>
        </main>
      </div>
      <Toaster />
    </EventsProvider>
  );
}
