import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, type RenderResult } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes } from "react-router";

export function renderWithProviders(
  ui: ReactElement,
  opts: { route?: string; path?: string } = {},
): RenderResult & { queryClient: QueryClient } {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const content = opts.path ? (
    <Routes>
      <Route path={opts.path} element={ui} />
    </Routes>
  ) : (
    ui
  );
  const result = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[opts.route ?? "/"]}>{content}</MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...result, queryClient };
}
