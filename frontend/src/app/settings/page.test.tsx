import { render, screen } from "@testing-library/react";
import { vi } from "vitest";

import SettingsPage from "./page";

vi.mock("next/navigation", () => ({
  usePathname: () => "/settings",
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }),
}));

vi.mock("@/lib/auth-client", () => ({
  useSession: () => ({
    data: { user: { id: "user-1", name: "Ada", email: "ada@example.com" } },
    isPending: false,
  }),
}));

vi.mock("@/hooks/use-social-access", () => ({
  useSocialAccess: () => false,
}));

vi.mock("@/lib/datafast", () => ({ track: vi.fn() }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const preferences = {
  fontFamily: "TikTokSans-Regular",
  fontSize: 24,
  fontColor: "#FFFFFF",
  notifyOnCompletion: true,
};

function billing(overrides: Record<string, unknown>) {
  return {
    monetization_enabled: true,
    plan: "free",
    subscription_status: "inactive",
    subscription_provider: null,
    usage_count: 0,
    usage_limit: 0,
    remaining: 0,
    usage_minutes: 0,
    minute_limit: 0,
    can_create_task: false,
    upgrade_required: true,
    reason: null,
    ...overrides,
  };
}

function mockFetches(summary: Record<string, unknown>) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("billing-summary")) return Response.json(summary);
    if (url.includes("/api/preferences")) return Response.json(preferences);
    if (url.includes("/api/fonts")) return Response.json({ fonts: [] });
    return Response.json({});
  }));
}

describe("settings plan section", () => {
  beforeEach(() => {
    vi.stubGlobal("ResizeObserver", class {
      observe() {}
      unobserve() {}
      disconnect() {}
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows plan cards for a free account", async () => {
    mockFetches(billing({}));
    render(<SettingsPage />);

    expect(await screen.findByRole("button", { name: /Choose Pro/ })).toBeInTheDocument();
    expect(screen.queryByText(/Your last payment failed/)).not.toBeInTheDocument();
  });

  it("replaces plan cards with a payment update for an unpaid Stripe subscription", async () => {
    mockFetches(billing({ subscription_status: "unpaid", subscription_provider: "stripe" }));
    render(<SettingsPage />);

    expect(await screen.findByText(/Your last payment failed/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Update payment method" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Choose Pro/ })).not.toBeInTheDocument();
    expect(screen.getByText(/Clipping is paused until this payment goes through/)).toBeInTheDocument();
  });

  it("keeps a past-due Stripe plan and does not offer a second checkout", async () => {
    mockFetches(billing({
      plan: "pro",
      subscription_status: "past_due",
      subscription_provider: "stripe",
      minute_limit: 600,
      usage_minutes: 12,
      can_create_task: true,
      upgrade_required: false,
      billing_interval: "month",
      output_short_side: 720,
    }));
    render(<SettingsPage />);

    expect(await screen.findByText(/Your last payment failed/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Manage Billing" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Choose Pro/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Upgrade" })).not.toBeInTheDocument();
  });
});
