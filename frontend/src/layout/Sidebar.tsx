import {
  Archive,
  ChartColumn,
  ChevronDown,
  ChevronRight,
  History,
  House,
  Plug,
  RefreshCw,
  Settings,
  type LucideIcon,
} from "lucide-react";
import { useState, type ReactNode } from "react";
import { Link, NavLink, useNavigate } from "react-router";
import { toast } from "sonner";
import { errorMessage } from "@/api/client";
import { useSignOut } from "@/auth/session";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { isSetUp, useConnections } from "@/features/connections/useConnections";
import { useSyncs } from "@/features/syncs/useSync";
import { DONATE_URL } from "@/config";
import { CONNECTION_STATUS_LABELS } from "@/copy";
import { readFlag, writeFlag } from "@/lib/storage";
import { cn } from "@/lib/utils";

// Hover is a neutral wash; selected is the warm accent plus the page's icon, so the two never read the same.
const linkClass = ({ isActive }: { isActive: boolean }) =>
  cn(
    "flex items-center justify-between gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-muted",
    isActive && "bg-accent font-medium text-accent-foreground hover:bg-accent",
  );

function NavItem({
  to,
  end,
  icon: Icon,
  trailing,
  className,
  children,
}: {
  to: string;
  end?: boolean;
  icon: LucideIcon;
  trailing?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <NavLink to={to} end={end} className={(s) => cn(linkClass(s), className)}>
      {({ isActive }) => (
        <>
          <span className="flex min-w-0 items-center gap-2">
            {isActive && <Icon aria-hidden className="size-4 shrink-0 text-primary" />}
            {children}
          </span>
          {trailing}
        </>
      )}
    </NavLink>
  );
}

function Group({
  label,
  to,
  icon,
  storageKey,
  noun,
  children,
}: {
  label: string;
  to: string;
  icon: LucideIcon;
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
        <NavItem to={to} end icon={icon} className="flex-1">
          {label}
        </NavItem>
        <button
          type="button"
          aria-label={`${open ? "Hide" : "Show"} ${noun}`}
          aria-expanded={open}
          onClick={toggle}
          className="flex items-center self-stretch rounded-md px-2 hover:bg-muted"
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
  const syncs = useSyncs();

  const services = (connections.data?.services ?? []).filter(isSetUp);

  return (
    <nav aria-label="Main" className="flex h-full flex-col gap-2 p-3">
      <NavItem to="/" end icon={House}>
        Home
      </NavItem>
      <Group label="Connections" to="/connections" icon={Plug} storageKey="linersync.sidebar.connections" noun="connections">
        {services.map((c) => (
          <li key={c.service}>
            <NavItem
              to={`/connections/${c.service}`}
              icon={Plug}
              trailing={
                <Badge variant={c.status === "connected" ? "secondary" : "outline"}>
                  {CONNECTION_STATUS_LABELS[c.status] ?? c.status}
                </Badge>
              }
            >
              <span className="truncate">{c.display_name}</span>
            </NavItem>
          </li>
        ))}
      </Group>
      <Group label="Syncs" to="/syncs" icon={RefreshCw} storageKey="linersync.sidebar.syncs" noun="syncs">
        {(syncs.data ?? []).map((s) => (
          <li key={s.sync_id}>
            <NavItem
              to={`/syncs/${s.sync_id}`}
              icon={RefreshCw}
              trailing={s.status === "paused" && <Badge variant="outline">Paused</Badge>}
            >
              <span className="truncate">{s.name}</span>
            </NavItem>
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
      </div>
      <NavItem to="/backup" icon={Archive}>
        Backup
      </NavItem>
      <NavItem to="/stats" icon={ChartColumn}>
        Stats
      </NavItem>
      <div className="mt-auto flex flex-col gap-1">
        <NavItem to="/history" icon={History}>
          History
        </NavItem>
        <NavItem to="/settings" icon={Settings}>
          Settings
        </NavItem>
        <Button
          variant="ghost"
          // Same padding as the nav links above so the label lines up with theirs.
          className="h-auto justify-start px-2 py-1.5"
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
