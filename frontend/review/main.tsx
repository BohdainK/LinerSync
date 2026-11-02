// Entry for the self-contained UI review build: the real app on recorded demo data, plus a review drawer.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { HashRouter } from "react-router";
import { AppRoutes } from "@/App";
import { TooltipProvider } from "@/components/ui/tooltip";
import { installMockApi } from "./mockApi";
import { ReviewPanel } from "./ReviewPanel";
import "./app.css";
import "./review.css";

installMockApi();

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        {/* Hash routing: the build is one file, opened without a server. */}
        <HashRouter>
          <AppRoutes />
          <ReviewPanel />
        </HashRouter>
      </TooltipProvider>
    </QueryClientProvider>
  </StrictMode>,
);
