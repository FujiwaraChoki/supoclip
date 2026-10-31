import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import { AppShell } from "./app-shell";

const router = vi.hoisted(() => ({ push: vi.fn() }));

vi.mock("next/navigation", () => ({
  usePathname: () => "/list",
  useRouter: () => ({ push: router.push, refresh: vi.fn(), replace: vi.fn() }),
}));

vi.mock("@/lib/auth-client", () => ({
  useSession: () => ({ data: { user: { id: "user-1", name: "Ada", email: "ada@example.com" } }, isPending: false }),
  signOut: vi.fn(),
}));

let workflowsEnabled: boolean;

beforeEach(() => {
  workflowsEnabled = true;
  router.push.mockReset();
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/api/tasks/workflows")) {
      return Response.json({
        enabled: workflowsEnabled,
        workflows: ["highlights", "series", "topic", "recap", "trailer"].map((id) => ({ id, available: workflowsEnabled || id === "highlights", min_source_seconds: 0 })),
        topic_max_chars: 120,
        instructions_max_chars: 500,
      });
    }
    if (url.includes("billing-summary")) return Response.json({ monetization_enabled: false });
    if (url.includes("/api/tasks")) return Response.json({ tasks: [] });
    if (url.includes("announcements")) return Response.json({ announcements: [] });
    if (url.includes("social")) return Response.json({ enabled: false });
    return Response.json({});
  }));
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function openPalette() {
  render(<AppShell><p>Library</p></AppShell>, { wrapper: TooltipProvider });
  fireEvent.keyDown(window, { key: "k", metaKey: true });
}

it("offers a shortcut to each workflow in the command palette", async () => {
  openPalette();
  expect(await screen.findByRole("option", { name: /New series/ })).toBeInTheDocument();
  for (const label of ["New topic search", "New recap", "New trailer"]) {
    expect(screen.getByRole("option", { name: new RegExp(label) })).toBeInTheDocument();
  }

  fireEvent.change(screen.getByRole("textbox"), { target: { value: "recap" } });
  expect(screen.getAllByRole("option")).toHaveLength(1);
  fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter" });
  expect(router.push).toHaveBeenCalledWith("/?workflow=recap");
});

it("leaves the shortcuts out when workflows are not enabled", async () => {
  workflowsEnabled = false;
  openPalette();
  expect(await screen.findByRole("option", { name: /New generation/ })).toBeInTheDocument();
  await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([input]) => String(input).includes("/api/tasks/workflows"))).toBe(true));
  await act(async () => {});
  expect(screen.queryByRole("option", { name: /New series/ })).not.toBeInTheDocument();
});
