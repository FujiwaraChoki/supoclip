import "@testing-library/jest-dom/vitest";
import type { ComponentType, ReactNode } from "react";
import { afterAll, afterEach, beforeAll, vi } from "vitest";
import { cleanup } from "@testing-library/react";
import { setupServer } from "msw/node";

vi.mock("@testing-library/react", async () => {
  const { readFileSync, readdirSync } = await import("node:fs");
  const { join } = await import("node:path");
  const actual = await vi.importActual<typeof import("@testing-library/react")>("@testing-library/react");
  const { createElement } = await import("react");
  const { NextIntlClientProvider } = await import("next-intl");
  const { QueryClient, QueryClientProvider } = await import("@tanstack/react-query");
  const dir = join(process.cwd(), "messages/en");
  const messages: Record<string, unknown> = {};
  for (const file of readdirSync(dir)) {
    if (!file.endsWith(".json")) continue;
    messages[file.replace(/\.json$/, "")] = JSON.parse(readFileSync(join(dir, file), "utf8"));
  }

  // Cleared after each test (below); the mock factory is hoisted, so the list lives on globalThis.
  const queryClients = ((globalThis as { __testQueryClients?: { clear(): void }[] }).__testQueryClients ??= []);

  function withIntl(options?: { wrapper?: ComponentType<{ children: ReactNode }> }) {
    const UserWrapper = options?.wrapper;
    // One cache per render() call, kept across rerender(), without retries, so tests never
    // share or wait on data.
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    queryClients.push(queryClient);
    return function IntlWrapper({ children }: { children: ReactNode }) {
      const inner = UserWrapper ? createElement(UserWrapper, null, children) : children;
      const withQuery = createElement(QueryClientProvider, { client: queryClient, children: inner });
      return createElement(NextIntlClientProvider, { locale: "en", messages, children: withQuery });
    };
  }

  return {
    ...actual,
    render: ((ui: Parameters<typeof actual.render>[0], options?: Parameters<typeof actual.render>[1]) =>
      actual.render(ui, { ...options, wrapper: withIntl(options) })) as typeof actual.render,
    renderHook: ((callback: never, options?: { wrapper?: ComponentType<{ children: ReactNode }> }) =>
      actual.renderHook(callback, { ...options, wrapper: withIntl(options) })) as typeof actual.renderHook,
  };
});

// Deliver query updates synchronously so `act()` sees them (the default batches them on a timer).
const { notifyManager } = await import("@tanstack/react-query");
notifyManager.setScheduler((callback) => callback());

afterEach(() => {
  const clients = (globalThis as { __testQueryClients?: { clear(): void }[] }).__testQueryClients ?? [];
  for (const client of clients.splice(0)) client.clear();
});

export const server = setupServer();

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  cleanup();
  server.resetHandlers();
});
afterAll(() => server.close());
