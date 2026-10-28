import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll } from "vitest";
import { FakeEventSource } from "./eventsource";
import { server } from "./server";

globalThis.EventSource = FakeEventSource as unknown as typeof EventSource;
// jsdom has no ResizeObserver; Radix's checkbox measures its hidden form input with one.
globalThis.ResizeObserver ??= class {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
};

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  cleanup();
  FakeEventSource.reset();
  server.resetHandlers();
});
afterAll(() => server.close());
