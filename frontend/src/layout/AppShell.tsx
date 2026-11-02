import { useQueryClient } from "@tanstack/react-query";
import { Menu, Moon, Sun } from "lucide-react";
import { Dialog as DialogPrimitive } from "radix-ui";
import { useEffect, useRef, useState } from "react";
import { Link, Navigate, Outlet, useLocation, useNavigate } from "react-router";
import { setUnauthorizedHandler } from "@/api/client";
import { EventsProvider } from "@/live/events";
import { isSetUp, useConnections } from "@/features/connections/useConnections";
import { ONBOARDING_SKIPPED_KEY, readFlag } from "@/lib/storage";
import { setTheme, useTheme } from "@/lib/theme";
import { Toaster } from "@/components/ui/sonner";
import { Sidebar } from "./Sidebar";
import { SunsetScene } from "./SunsetScene";

export function AppShell() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();
  // The mobile menu stays open only on the page it was opened from, so any navigation closes it.
  const [menuOpenAt, setMenuOpenAt] = useState<string | null>(null);
  const theme = useTheme();
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
      <div className="flex min-h-screen flex-col">
        <header className="flex h-14 shrink-0 items-center gap-2 bg-header px-4 text-header-foreground">
          <DialogPrimitive.Root
            open={menuOpenAt === location.key}
            onOpenChange={(open) => setMenuOpenAt(open ? location.key : null)}
          >
            <DialogPrimitive.Trigger
              aria-label="Open menu"
              className="-ml-2 rounded-md p-2 hover:bg-header-foreground/10 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none md:hidden"
            >
              <Menu className="size-5" />
            </DialogPrimitive.Trigger>
            <DialogPrimitive.Portal>
              <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/40 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:animate-in data-[state=open]:fade-in-0 motion-reduce:animate-none" />
              <DialogPrimitive.Content
                aria-describedby={undefined}
                className="fixed inset-y-0 left-0 z-50 flex w-72 max-w-[85vw] flex-col bg-background shadow-lg data-[state=closed]:animate-out data-[state=closed]:slide-out-to-left data-[state=open]:animate-in data-[state=open]:slide-in-from-left motion-reduce:animate-none"
              >
                <DialogPrimitive.Title className="sr-only">Menu</DialogPrimitive.Title>
                <div className="min-h-0 flex-1 overflow-y-auto">
                  <Sidebar />
                </div>
                <SunsetScene compact className="aspect-[60/19] w-full shrink-0" />
              </DialogPrimitive.Content>
            </DialogPrimitive.Portal>
          </DialogPrimitive.Root>
          <Link to="/" className="rounded-sm font-serif text-xl italic focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none">
            LinerSync
          </Link>
          <button
            type="button"
            aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            className="-mr-2 ml-auto rounded-md p-2 hover:bg-header-foreground/10 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
          >
            {theme === "dark" ? <Sun className="size-5" /> : <Moon className="size-5" />}
          </button>
        </header>
        <div className="flex flex-1">
          <aside className="hidden w-64 shrink-0 border-r md:block">
            <Sidebar />
          </aside>
          <main className="min-w-0 flex-1 p-4 md:p-6">
            <div className="mx-auto max-w-6xl">
              <Outlet />
            </div>
          </main>
        </div>
        <footer className="hidden md:block">
          {/* Grows with the window so the drawing is never cropped at the top, only at the sides. */}
          <SunsetScene className="h-[max(12rem,calc(100vw*200/1440))] w-full" />
        </footer>
      </div>
      <Toaster />
    </EventsProvider>
  );
}
