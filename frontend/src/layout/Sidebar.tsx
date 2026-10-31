import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Link, NavLink, useNavigate } from "react-router";
import { toast } from "sonner";
import { api, call, errorMessage } from "@/api/client";
import { keys } from "@/api/keys";
import { useSignOut } from "@/auth/session";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useConnections } from "@/features/connections/useConnections";
import { DONATE_URL } from "@/config";
import { CONNECTION_STATUS_LABELS } from "@/copy";
import { readFlag, writeFlag } from "@/lib/storage";
import { cn } from "@/lib/utils";

const linkClass = ({ isActive }: { isActive: boolean }) =>
  cn("flex items-center justify-between gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-accent", isActive && "bg-accent font-medium");

function Group({
  label,
  to,
  storageKey,
  noun,
  children,
}: {
  label: string;
  to: string;
  storageKey: string;
  noun: string;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(() => readFlag(storageKey) !== "closed");
  const toggle = () => {
    writeFlag(storageKey, open ? "closed" : "open");
    setOpen(!open);
  };
  const Chevron = open ? ChevronDown : ChevronRight;
  return (
    <div>
      <div className="flex items-center">
        <NavLink to={to} end className={(s) => cn(linkClass(s), "flex-1")}>
          {label}
        </NavLink>
        <button
          type="button"
          aria-label={`${open ? "Hide" : "Show"} ${noun}`}
          aria-expanded={open}
          onClick={toggle}
          className="rounded-md p-1.5 hover:bg-accent"
        >
          <Chevron className="size-4" />
        </button>
      </div>
      {open && <ul className="ml-3 flex flex-col border-l pl-2">{children}</ul>}
    </div>
  );
}

export function Sidebar() {
  const navigate = useNavigate();
  const signOut = useSignOut();
  const connections = useConnections();
  const syncs = useQuery({ queryKey: keys.syncs, queryFn: () => call(api.GET("/api/syncs")) });

  const services = (connections.data?.services ?? []).filter((c) => c.status !== "not_set_up");

  return (
    <nav aria-label="Main" className="flex h-full flex-col gap-2 p-3">
      <NavLink to="/" end className={linkClass}>
        Home
      </NavLink>
      <Group label="Connections" to="/connections" storageKey="linersync.sidebar.connections" noun="connections">
        {services.map((c) => (
          <li key={c.service}>
            <NavLink to={`/connections/${c.service}`} className={linkClass}>
              <span className="truncate">{c.display_name}</span>
              <Badge variant={c.status === "connected" ? "secondary" : "outline"}>
                {CONNECTION_STATUS_LABELS[c.status] ?? c.status}
              </Badge>
            </NavLink>
          </li>
        ))}
      </Group>
      <Group label="Syncs" to="/syncs" storageKey="linersync.sidebar.syncs" noun="syncs">
        {(syncs.data ?? []).map((s) => (
          <li key={s.sync_id}>
            <NavLink to={`/syncs/${s.sync_id}`} className={linkClass}>
              <span className="truncate">{s.name}</span>
              {s.status === "paused" && <Badge variant="outline">Paused</Badge>}
            </NavLink>
          </li>
        ))}
      </Group>
      <div className="flex flex-col gap-1">
        <Button variant="outline" className="justify-start" onClick={() => navigate("/syncs/new")}>
          + New sync
        </Button>
        <Button variant="outline" className="justify-start" onClick={() => navigate("/transfers/new")}>
          + New transfer
        </Button>
        <Button variant="ghost" className="justify-start" onClick={() => navigate("/backup")}>
          Backup
        </Button>
      </div>
      <NavLink to="/stats" className={linkClass}>
        Stats
      </NavLink>
      <div className="mt-auto flex flex-col gap-1">
        <NavLink to="/settings" className={linkClass}>
          Settings
        </NavLink>
        <Button
          variant="ghost"
          className="justify-start"
          onClick={() => signOut().catch((e: unknown) => toast.error(errorMessage(e)))}
        >
          Sign out
        </Button>
        {DONATE_URL && (
          <Link to={DONATE_URL} target="_blank" rel="noreferrer" className="px-2 text-xs text-muted-foreground underline">
            Donate
          </Link>
        )}
      </div>
    </nav>
  );
}
