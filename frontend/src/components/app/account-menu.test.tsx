import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ThemeProvider as NextThemesProvider } from "next-themes";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type { BillingSummary } from "@/hooks/use-billing-summary";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AccountMenu } from "./account-menu";

const auth = vi.hoisted(() => ({ signOut: vi.fn() }));
vi.mock("@/lib/auth-client", () => ({ signOut: auth.signOut }));
vi.mock("next/navigation", () => ({ usePathname: () => "/list", useRouter: () => ({ push: vi.fn() }) }));

const USER = { id: "user_example_1", name: "Alex Rivera", email: "alex.rivera@example.com" };
const TEAM_ID = "team_example_1";

function summary(patch: Partial<BillingSummary>): BillingSummary {
  return {
    monetization_enabled: true, plan: "pro", subscription_status: "active", usage_count: 0, usage_limit: null,
    remaining: null, can_create_task: true, upgrade_required: false, reason: null, workspace_type: "personal",
    usage_minutes: 312, minute_limit: 600, remaining_minutes: 288, ...patch,
  };
}

const teamSummary = summary({
  workspace_type: "team", team_id: TEAM_ID, team_name: "Northwind Studio", plan: "team",
  usage_minutes: 1840, minute_limit: 3600, remaining_minutes: 1760,
  member_minute_limit: 600, member_usage_minutes: 120, member_remaining_minutes: 480,
});

let storage: Map<string, string>;

beforeEach(() => {
  storage = new Map();
  vi.stubGlobal("localStorage", {
    getItem: vi.fn((key: string) => storage.get(key) ?? null),
    setItem: vi.fn((key: string, value: string) => { storage.set(key, value); }),
    removeItem: vi.fn((key: string) => { storage.delete(key); }),
  });
  vi.stubGlobal("matchMedia", () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }));
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/api/billing/usage")) {
      return Response.json({
        activeTeamId: TEAM_ID,
        workspaces: [
          { teamId: null, name: null, role: null, summary: summary({}) },
          { teamId: TEAM_ID, name: "Northwind Studio", role: "member", summary: teamSummary },
        ],
      });
    }
    if (url.includes("/api/announcements")) {
      return Response.json({ announcements: [{ id: "a1", title: "Teams", description: "Pool minutes.", action: "Open", href: "/settings/team", publishedAt: "2026-11-20T00:00:00Z" }] });
    }
    return Response.json({});
  }));
});

afterEach(() => {
  vi.unstubAllGlobals();
  document.documentElement.className = "";
});

function renderMenu(billing: BillingSummary | null) {
  return render(
    <NextThemesProvider attribute="class" defaultTheme="light" enableSystem={false}>
      <TooltipProvider>
        <AccountMenu user={USER} billing={billing} compact={false} />
      </TooltipProvider>
    </NextThemesProvider>,
  );
}

it("shows minutes left under the name and keeps What's new beside it", async () => {
  renderMenu(summary({}));
  expect(screen.getByText("Alex Rivera")).toBeInTheDocument();
  expect(screen.getByText("Pro · 288 min left")).toBeInTheDocument();
  expect(await screen.findByRole("button", { name: /What's new, 1 unread announcement/ })).toBeInTheDocument();
});

it("shows the tighter of the team pool and the member limit", () => {
  renderMenu(teamSummary);
  expect(screen.getByText("480 min left")).toBeInTheDocument();
});

it("shows the email instead of usage when the instance is not metered", () => {
  renderMenu(summary({ monetization_enabled: false }));
  expect(screen.getByText("alex.rivera@example.com")).toBeInTheDocument();
  expect(screen.queryByText(/min left/)).not.toBeInTheDocument();
});

it("opens a menu with team actions, theme and sign out", async () => {
  const user = userEvent.setup();
  renderMenu(teamSummary);
  await user.click(screen.getByText("Alex Rivera"));

  expect(await screen.findByRole("menuitem", { name: "Team settings" })).toHaveAttribute("href", "/settings/team");
  expect(screen.getByRole("menuitem", { name: "Create a team" })).toBeInTheDocument();
  expect(screen.getByText("alex.rivera@example.com")).toBeInTheDocument();

  await user.click(screen.getByRole("menuitemradio", { name: "Dark" }));
  await waitFor(() => expect(document.documentElement).toHaveClass("dark"));
  expect(screen.getByRole("menuitemradio", { name: "Dark" })).toHaveAttribute("aria-checked", "true");

  await user.click(screen.getByRole("menuitem", { name: "Sign out" }));
  expect(auth.signOut).toHaveBeenCalled();
});

it("lists every workspace's limit in the usage submenu, current one first", async () => {
  const user = userEvent.setup();
  renderMenu(teamSummary);
  await user.click(screen.getByText("Alex Rivera"));
  await user.click(await screen.findByRole("menuitem", { name: /Usage/ }));

  expect(await screen.findByText("Personal")).toBeInTheDocument();
  const blocks = screen.getAllByText(/Northwind Studio|Personal/).map((node) => node.textContent);
  expect(blocks).toEqual(["Northwind Studio", "Personal"]);
  expect(screen.getByText("Your limit")).toBeInTheDocument();
  expect(screen.getByText("120 / 600 min")).toBeInTheDocument();
  expect(screen.getByText("Team pool 1,840 / 3,600 min")).toBeInTheDocument();
  expect(screen.getByText("Pro plan")).toBeInTheDocument();
  expect(screen.getByRole("menuitem", { name: "Billing and usage" })).toHaveAttribute("href", "/settings/team");
  expect(fetch).toHaveBeenCalledWith("/api/billing/usage", expect.anything());
});

it("asks a member near their own limit to talk to an admin", async () => {
  const user = userEvent.setup();
  const nearly = { ...teamSummary, member_usage_minutes: 562, member_remaining_minutes: 38 };
  vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) =>
    String(input).includes("/api/billing/usage")
      ? Response.json({ activeTeamId: TEAM_ID, workspaces: [{ teamId: TEAM_ID, name: "Northwind Studio", role: "member", summary: nearly }] })
      : Response.json({ announcements: [] }),
  );
  renderMenu(nearly);
  expect(screen.getByText("38 min left")).toHaveClass("text-brand");
  await user.click(screen.getByText("Alex Rivera"));
  await user.click(await screen.findByRole("menuitem", { name: /Usage/ }));
  expect(await screen.findByText("Ask an admin for more")).toBeInTheDocument();
});
