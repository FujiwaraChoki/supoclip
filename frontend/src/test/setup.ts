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
  const dir = join(process.cwd(), "messages/en");
  const messages: Record<string, unknown> = {};
  for (const file of readdirSync(dir)) {
    if (!file.endsWith(".json")) continue;
    messages[file.replace(/\.json$/, "")] = JSON.parse(readFileSync(join(dir, file), "utf8"));
  }

  function withIntl(options?: { wrapper?: ComponentType<{ children: ReactNode }> }) {
    const UserWrapper = options?.wrapper;
    return function IntlWrapper({ children }: { children: ReactNode }) {
      const inner = UserWrapper ? createElement(UserWrapper, null, children) : children;
      return createElement(NextIntlClientProvider, { locale: "en", messages, children: inner });
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

export const server = setupServer();

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  cleanup();
  server.resetHandlers();
});
afterAll(() => server.close());
