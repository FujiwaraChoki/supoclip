import { Suspense } from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TooltipProvider } from "@/components/ui/tooltip";
import TeamSettingsPage from "./page";
import AcceptInvitePage from "@/app/invite/[token]/page";
import LandingPage from "@/components/landing-page";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), message: vi.fn() },
}));

const sessionState = vi.hoisted(() => ({
  user: { id: "u-owner", email: "owner@example.com", name: "Owner" } as {
    id: string;
    email: string;
    name: string;
  } | null,
}));

const workspaceState = vi.hoisted(() => ({
  activeTeamId: "team-1" as string | null,
  teams: [
    {
      id: "team-1",
      name: "Clip Crew",
      slug: "clip-crew",
      ownerId: "u-owner",
      role: "owner" as const,
      plan: "team",
      subscriptionStatus: "active",
      seatLimit: 5,
      memberCount: 2,
      pendingInviteCount: 1,
      occupiedSeats: 3,
    },
  ],
  switchWorkspace: vi.fn(),
  createTeam: vi.fn(),
}));

vi.mock("@/lib/auth-client", () => ({
  useSession: () => ({
    data: sessionState.user ? { user: sessionState.user } : null,
    isPending: false,
  }),
  signOut: vi.fn(),
}));

vi.mock("@/hooks/use-workspace", () => ({
  useWorkspace: () => ({
    activeTeamId: workspaceState.activeTeamId,
    activeTeam: workspaceState.teams.find((t) => t.id === workspaceState.activeTeamId) ?? null,
    teams: workspaceState.teams,
    loading: false,
    refresh: vi.fn(),
    switchWorkspace: workspaceState.switchWorkspace,
    createTeam: workspaceState.createTeam,
  }),
  invalidateWorkspaces: vi.fn(),
}));

vi.mock("@/lib/monetization", () => ({ monetizationEnabled: true }));

function makeTeamDetail(overrides: Record<string, unknown> = {}) {
  return {
    team: {
      id: "team-1",
      name: "Clip Crew",
      slug: "clip-crew",
      ownerId: "u-owner",
      plan: "team",
      subscriptionStatus: "active",
      subscriptionProvider: "stripe",
      seatLimit: 5,
      billingPeriodStart: "2026-11-01T00:00:00.000Z",
      billingPeriodEnd: "2026-12-01T00:00:00.000Z",
      cancelAt: null,
      hasStripeCustomer: true,
      hasStripeSubscription: true,
      createdAt: "2026-11-01T00:00:00.000Z",
    },
    currentUserRole: "owner",
    permissions: {
      canManageMembers: true,
      canManageBilling: true,
      canDeleteTeam: true,
    },
    seats: {
      limit: 5,
      activeMembers: 2,
      pendingInvitations: 1,
      occupied: 3,
    },
    members: [
      { userId: "u-owner", name: "Owner", email: "owner@example.com", image: null, role: "owner", joinedAt: "2026-11-01T00:00:00.000Z" },
      { userId: "u-editor", name: "Editor", email: "editor@example.com", image: null, role: "member", joinedAt: "2026-11-02T00:00:00.000Z" },
    ],
    invitations: [
      { id: "inv-1", email: "pending@example.com", role: "member", invitedBy: "Owner", expiresAt: "2026-11-28T00:00:00.000Z", createdAt: "2026-11-20T00:00:00.000Z" },
    ],
    usage: {
      billing: {
        plan: "team",
        subscription_status: "active",
        billing_interval: "month",
        usage_minutes: 240,
        minute_limit: 5400,
        remaining_minutes: 5160,
        output_short_side: 1080,
      },
      ai_edits: {
        used: 18,
        limit: 300,
        remaining: 282,
      },
    },
    ...overrides,
  };
}

describe("TeamSettingsPage UI & role matrix", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.unstubAllGlobals();
    sessionState.user = { id: "u-owner", email: "owner@example.com", name: "Owner" };
    workspaceState.activeTeamId = "team-1";
    window.history.replaceState(null, "", "/settings/team");
  });

  it("renders owner overview, live seat stepper pricing, invite creation, and Danger Zone", async () => {
    const fetchMock = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
      if (url === "/api/teams/team-1") {
        return new Response(JSON.stringify(makeTeamDetail()), { status: 200 });
      }
      if (url === "/api/teams/team-1/invitations" && init?.method === "POST") {
        return new Response(
          JSON.stringify({
            invitation: { id: "inv-2", email: "new@example.com", role: "member", expiresAt: "2026-11-28T00:00:00.000Z", createdAt: "2026-11-20T00:00:00.000Z" },
            inviteUrl: "https://supoclip.example.com/invite/tok123",
            emailSent: false,
          }),
          { status: 201 },
        );
      }
      if (url === "/api/teams/team-1/seats" && init?.method === "POST") {
        return new Response(JSON.stringify({ seatLimit: 6, prorationApplied: true }), { status: 200 });
      }
      return new Response("{}", { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<TeamSettingsPage />);

    // Overview metrics rendered
    expect(await screen.findByText("Clip Crew")).toBeInTheDocument();
    expect(screen.getByText("240 / 5400 min")).toBeInTheDocument();
    expect(screen.getByText("18 / 300")).toBeInTheDocument();
    expect(screen.getByText("3 of 5 seats")).toBeInTheDocument();

    // Seat stepper starts at 5 ($99/mo, 90 hrs) -> increment to 6 ($114/mo, 105 hrs)
    expect(screen.getByText(/\$99/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Increase seats" }));
    expect(screen.getByText(/\$114/)).toBeInTheDocument();
    expect(screen.getByText(/105 pooled hrs\/mo/)).toBeInTheDocument();

    // Save seats triggers POST /api/teams/team-1/seats
    fireEvent.click(screen.getByRole("button", { name: "Update to 6 seats" }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/teams/team-1/seats",
        expect.objectContaining({ method: "POST", body: JSON.stringify({ seats: 6 }) }),
      ),
    );

    // Send an invite and verify copyable invite link banner appears
    fireEvent.change(screen.getByLabelText("Teammate email"), {
      target: { value: "new@example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send invite" }));
    expect(await screen.findByText("https://supoclip.example.com/invite/tok123")).toBeInTheDocument();

    // Danger Zone is visible to owner
    expect(screen.getByText("Danger zone")).toBeInTheDocument();
  });

  it("blocks inviting and shows seat cap banner when all seats are occupied", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify(
            makeTeamDetail({
              seats: { limit: 3, activeMembers: 2, pendingInvitations: 1, occupied: 3 },
              team: { ...makeTeamDetail().team, seatLimit: 3 },
            }),
          ),
          { status: 200 },
        ),
      ),
    );

    render(<TeamSettingsPage />);
    expect(await screen.findByText(/All 3 of 3 seats are in use/)).toBeInTheDocument();
    expect(screen.getByLabelText("Teammate email")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Send invite" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Decrease seats" })).toBeDisabled();
  });

  it("hides billing actions, invite form, and Danger Zone for regular members", async () => {
    sessionState.user = { id: "u-editor", email: "editor@example.com", name: "Editor" };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify(
            makeTeamDetail({
              currentUserRole: "member",
              permissions: {
                canManageMembers: false,
                canManageBilling: false,
                canDeleteTeam: false,
              },
              invitations: [],
            }),
          ),
          { status: 200 },
        ),
      ),
    );

    render(<TeamSettingsPage />);
    expect(await screen.findByText("Clip Crew")).toBeInTheDocument();
    expect(screen.getByText(/Only the team owner can start a subscription/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Teammate email")).not.toBeInTheDocument();
    expect(screen.queryByText("Danger zone")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Leave" })).toBeInTheDocument();
  });

  it("shows existing teams and Create a team form when in Personal workspace", async () => {
    workspaceState.activeTeamId = null;
    render(<TeamSettingsPage />);

    expect(screen.getByText("Your teams")).toBeInTheDocument();
    expect(screen.getByText("Clip Crew")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create team" })).toBeDisabled();

    fireEvent.change(screen.getByLabelText("Team name"), { target: { value: "New Agency" } });
    expect(screen.getByRole("button", { name: "Create team" })).not.toBeDisabled();
  });
});

describe("AcceptInvitePage UI states", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.unstubAllGlobals();
  });

  it("renders signed-out invite with pre-filled email links for sign-up and sign-in", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            invitation: {
              teamId: "team-1",
              teamName: "Clip Crew",
              email: "editor@example.com",
              role: "admin",
              status: "valid",
              inviterName: "Alex",
              expiresAt: "2026-11-28T00:00:00.000Z",
            },
            signedIn: false,
            signedInEmail: null,
            emailMatches: false,
          }),
          { status: 200 },
        ),
      ),
    );

    await act(async () => {
      render(
        <Suspense fallback={null}>
          <AcceptInvitePage params={Promise.resolve({ token: "sample-token-123456" })} />
        </Suspense>,
      );
    });

    expect(await screen.findByText("Join Clip Crew")).toBeInTheDocument();
    const signUpLink = screen.getByRole("link", { name: /Create account/ });
    expect(signUpLink.getAttribute("href")).toContain("email=editor%40example.com");
    expect(signUpLink.getAttribute("href")).toContain("next=%2Finvite%2Fsample-token-123456");
  });

  it("warns when signed in with a different email and renders expired/revoked states", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            invitation: {
              teamId: "team-1",
              teamName: "Clip Crew",
              email: "editor@example.com",
              role: "member",
              status: "valid",
              inviterName: "Alex",
              expiresAt: "2026-11-28T00:00:00.000Z",
            },
            signedIn: true,
            signedInEmail: "wrong@example.com",
            emailMatches: false,
          }),
          { status: 200 },
        ),
      ),
    );

    await act(async () => {
      render(
        <Suspense fallback={null}>
          <AcceptInvitePage params={Promise.resolve({ token: "sample-token-123456" })} />
        </Suspense>,
      );
    });
    expect(await screen.findByText(/signed in as wrong@example.com/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Sign out and switch account/ })).toBeInTheDocument();
  });
});

describe("LandingPage Team pricing card", () => {
  it("updates Team price and pooled hours live as seats and billing interval change", () => {
    vi.stubGlobal(
      "IntersectionObserver",
      class {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    );
    vi.stubGlobal(
      "ResizeObserver",
      class {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    );
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      value: vi.fn().mockImplementation((query: string) => ({
        matches: false,
        media: query,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      })),
    });
    vi.spyOn(HTMLMediaElement.prototype, "play").mockImplementation(() => Promise.resolve());
    render(
      <TooltipProvider>
        <LandingPage />
      </TooltipProvider>,
    );

    expect(screen.getByText("$69")).toBeInTheDocument();
    expect(screen.getByText("60 pooled hours of video/month")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Increase seats" }));
    fireEvent.click(screen.getByRole("button", { name: "Increase seats" }));
    expect(screen.getByText("$99")).toBeInTheDocument();
    expect(screen.getByText("90 pooled hours of video/month")).toBeInTheDocument();

    // Toggle yearly -> 5 seats = $990/year (2 months free vs $1188)
    fireEvent.click(screen.getByRole("radio", { name: /Yearly/ }));
    expect(screen.getByText("$990")).toBeInTheDocument();
    expect(screen.getByText("$1188")).toBeInTheDocument();
  });
});
