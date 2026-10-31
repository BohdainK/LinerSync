import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { BrowserRouter, Route, Routes } from "react-router";
import { ApiError } from "@/api/client";
import { LoginPage } from "@/auth/LoginPage";
import { RequireSession } from "@/auth/RequireSession";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ConnectionPage } from "@/features/connections/ConnectionPage";
import { ConnectionsPage } from "@/features/connections/ConnectionsPage";
import { BackupEntryPage } from "@/features/backup/BackupEntryPage";
import { BackupPage } from "@/features/backup/BackupPage";
import { SettingsPage } from "@/features/settings/SettingsPage";
import { HomePage } from "@/features/home/HomePage";
import { NewSyncPage } from "@/features/new-sync/NewSyncPage";
import { NewTransferPage } from "@/features/transfers/NewTransferPage";
import { RunPage } from "@/features/runs/RunPage";
import { StatsPage } from "@/features/stats/StatsPage";
import { SyncPage } from "@/features/syncs/SyncPage";
import { SyncsPage } from "@/features/syncs/SyncsPage";
import { OnboardingPage } from "@/features/connections/OnboardingPage";
import { AppShell } from "@/layout/AppShell";
import { NotFoundPage } from "@/layout/NotFoundPage";

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<RequireSession />}>
        <Route element={<AppShell />}>
          <Route index element={<HomePage />} />
          <Route path="onboarding" element={<OnboardingPage />} />
          <Route path="connections" element={<ConnectionsPage />} />
          <Route path="connections/:service" element={<ConnectionPage />} />
          <Route path="syncs" element={<SyncsPage />} />
          <Route path="syncs/new" element={<NewSyncPage />} />
          <Route path="syncs/:syncId" element={<SyncPage />} />
          <Route path="runs/:runId" element={<RunPage />} />
          <Route path="transfers/new" element={<NewTransferPage />} />
          <Route path="backup" element={<BackupPage />} />
          <Route path="backup/playlists/:entryId" element={<BackupEntryPage />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="stats" element={<StatsPage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Route>
    </Routes>
  );
}

export default function App() {
  const [queryClient] = useState(() => new QueryClient({
      defaultOptions: {
        queries: {
          // Client errors (401, 404, 422...) won't change on retry; retrying a 401 would also
          // fire the unauthorized handler repeatedly.
          retry: (count, error) =>
            !(error instanceof ApiError && error.status >= 400 && error.status < 500) && count < 2,
        },
      },
    }));
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <BrowserRouter>
          <AppRoutes />
        </BrowserRouter>
      </TooltipProvider>
    </QueryClientProvider>
  );
}
