import { Suspense } from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TooltipProvider } from "@/components/ui/tooltip";
import TeamSettingsPage from "./page";
import AcceptInvitePage from "@/app/invite/[token]/page";
import LandingPage from "@/components/landing-page";
import { WorkspaceSwitcher } from "@/components/workspace/workspace-switcher";
import { toast } from "sonner";
import { invalidateWorkspaces } from "@/hooks/use-workspace";

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
  teamsAvailable: true,
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
    teamsAvailable: workspaceState.teamsAvailable,
    loading: false,
    refresh: vi.fn(),
    switchWorkspace: workspaceState.switchWorkspace,
    createTeam: workspaceState.createTeam,
  }),
  useTeamRole: () => null,
  WorkspaceRequestError: class WorkspaceRequestError extends Error {},
  announceWorkspaceSwitch: vi.fn(),
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
    workspaceState.teamsAvailable = true;
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

  it("keeps existing teams but offers no new one where the Team plan cannot be sold yet", () => {
    workspaceState.activeTeamId = null;
    workspaceState.teamsAvailable = false;
    render(<TeamSettingsPage />);

    expect(screen.getByText("Clip Crew")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Open team/ })).toBeInTheDocument();
    expect(screen.getByText("Team workspaces aren't available yet.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create team" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Team name")).not.toBeInTheDocument();
  });

  it("lets an admin invite members only, without a role to choose", async () => {
    sessionState.user = { id: "u-editor", email: "editor@example.com", name: "Editor" };
    const fetchMock = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
      if (url === "/api/teams/team-1/invitations" && init?.method === "POST") {
        return new Response(JSON.stringify({ inviteUrl: "https://supoclip.example.com/invite/tok456", emailSent: true }), { status: 201 });
      }
      return new Response(
        JSON.stringify(makeTeamDetail({
          currentUserRole: "admin",
          permissions: { canManageMembers: true, canManageBilling: false, canDeleteTeam: false },
        })),
        { status: 200 },
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<TeamSettingsPage />);
    fireEvent.change(await screen.findByLabelText("Teammate email"), { target: { value: "new@example.com" } });
    expect(screen.queryByLabelText("Role")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Send invite" }));

    await waitFor(() => expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST")).toBe(true));
    const [, init] = fetchMock.mock.calls.find(([, options]) => options?.method === "POST")!;
    expect(JSON.parse(String(init.body))).toEqual({ email: "new@example.com", role: "member" });
  });

  it("keeps the page and an unsaved name when a background refresh fails, and offers a retry when the first load does", async () => {
    let failing = false;
    const fetchMock = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") return new Response(JSON.stringify({ seatLimit: 6 }), { status: 200 });
      if (failing) return new Response(JSON.stringify({ error: "Service unavailable" }), { status: 503 });
      return new Response(JSON.stringify(makeTeamDetail()), { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);

    const view = render(<TeamSettingsPage />);
    expect(await screen.findByRole("heading", { name: "Clip Crew" })).toBeInTheDocument();
    fireEvent.change(screen.getByDisplayValue("Clip Crew"), { target: { value: "Clip Crew Two" } });

    // A seat change reloads the team in the background; that reload fails.
    failing = true;
    fireEvent.click(screen.getByRole("button", { name: "Increase seats" }));
    fireEvent.click(screen.getByRole("button", { name: "Update to 6 seats" }));
    await waitFor(() => expect(fetchMock.mock.calls.filter(([url, init]) => url === "/api/teams/team-1" && !init?.method).length).toBeGreaterThan(1));
    expect(screen.getByRole("heading", { name: "Clip Crew" })).toBeInTheDocument();
    expect(screen.queryByText("Service unavailable")).not.toBeInTheDocument();
    expect(screen.getByDisplayValue("Clip Crew Two")).toBeInTheDocument();
    view.unmount();

    // A first load that fails says so and can be tried again.
    render(<TeamSettingsPage />);
    expect(await screen.findByText("Service unavailable")).toBeInTheDocument();
    failing = false;
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("heading", { name: "Clip Crew" })).toBeInTheDocument();
  });

  it("lets owner configure per-member generation limits via dialog presets and hides Limits from members", async () => {
    const fetchMock = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
      if (init?.method === "PATCH" && url.includes("/members/u-2")) {
        return new Response(
          JSON.stringify({
            member: {
              teamId: "team-1",
              userId: "u-2",
              role: "member",
              monthlyMinuteLimit: 600,
              monthlyAiEditLimit: 50,
            },
          }),
          { status: 200 },
        );
      }
      return new Response(
        JSON.stringify(
          makeTeamDetail({
            members: [
              {
                userId: "u-owner",
                name: "Alex Owner",
                email: "owner@example.com",
                image: null,
                role: "owner",
                joinedAt: "2026-11-01T00:00:00.000Z",
                monthlyMinuteLimit: null,
                monthlyAiEditLimit: null,
                usageMinutes: 120,
                usageAiEdits: 4,
              },
              {
                userId: "u-2",
                name: "Sam Editor",
                email: "editor@example.com",
                image: null,
                role: "member",
                joinedAt: "2026-11-02T00:00:00.000Z",
                monthlyMinuteLimit: 300,
                monthlyAiEditLimit: 25,
                usageMinutes: 140,
                usageAiEdits: 8,
              },
            ],
          }),
        ),
        { status: 200 },
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<TeamSettingsPage />);
    expect(await screen.findByText("140 / 300 min")).toBeInTheDocument();
    expect(screen.getByText("8 / 25 AI edits")).toBeInTheDocument();

    // Only non-owner gets a Limits button
    const limitsButtons = screen.getAllByRole("button", { name: /Limits/ });
    expect(limitsButtons).toHaveLength(1);
    fireEvent.click(limitsButtons[0]);

    expect(await screen.findByText("Member generation limits")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "10 h (600 min)" }));
    fireEvent.click(screen.getByRole("button", { name: "50" }));
    fireEvent.click(screen.getByRole("button", { name: "Save limits" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/teams/team-1/members/u-2",
        expect.objectContaining({
          method: "PATCH",
          body: JSON.stringify({ monthlyMinuteLimit: 600, monthlyAiEditLimit: 50 }),
        }),
      );
    });
  });

  it("hides WorkspaceSwitcher for solo users with zero teams and renders it once they have a team", () => {
    workspaceState.teams = [];
    workspaceState.activeTeamId = null;
    const { container, rerender } = render(<WorkspaceSwitcher />);
    expect(container.firstChild).toBeNull();

    workspaceState.teams = [
      {
        id: "team-1",
        name: "Clip Crew",
        slug: "clip-crew",
        ownerId: "u-1",
        role: "owner",
        plan: "team",
        subscriptionStatus: "active",
        seatLimit: 5,
        memberCount: 2,
        pendingInviteCount: 1,
        occupiedSeats: 3,
      },
    ];
    workspaceState.activeTeamId = "team-1";
    rerender(<WorkspaceSwitcher />);
    expect(screen.getByRole("button", { name: "Switch workspace" })).toBeInTheDocument();
  });
  it("after checkout, reloads the team until its plan is paid, then says so once", async () => {
    window.history.replaceState(null, "", "/settings/team?billing=success");
    let detailCalls = 0;
    const fetchMock = vi.fn().mockImplementation(async () => {
      detailCalls += 1;
      // The redirect beats Stripe's webhook: the first answer is still unpaid.
      const team = detailCalls === 1
        ? { ...makeTeamDetail().team, plan: "free", subscriptionStatus: "inactive", hasStripeSubscription: false }
        : makeTeamDetail().team;
      return new Response(JSON.stringify(makeTeamDetail({ team })), { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<TeamSettingsPage />);

    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Team plan is active!"), { timeout: 4000 });
    expect(window.location.search).toBe("");
    expect(invalidateWorkspaces).toHaveBeenCalled();
    const calls = fetchMock.mock.calls.length;
    await new Promise((resolve) => setTimeout(resolve, 1700));
    // Polling stopped once the plan was paid.
    expect(fetchMock.mock.calls.length).toBe(calls);
    expect(toast.success).toHaveBeenCalledTimes(1);
  });
  it("after checkout, a slower first load never lands the unpaid team over the paid one", async () => {
    window.history.replaceState(null, "", "/settings/team?billing=success");
    let detailCalls = 0;
    const fetchMock = vi.fn().mockImplementation(async () => {
      detailCalls += 1;
      if (detailCalls === 1) {
        // The first load was sent before Stripe's webhook and answers after the poll does.
        await new Promise((resolve) => setTimeout(resolve, 200));
        const team = { ...makeTeamDetail().team, plan: "free", subscriptionStatus: "inactive", hasStripeSubscription: false };
        return new Response(JSON.stringify(makeTeamDetail({ team })), { status: 200 });
      }
      return new Response(JSON.stringify(makeTeamDetail()), { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<TeamSettingsPage />);

    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Team plan is active!"));
    await new Promise((resolve) => setTimeout(resolve, 400));
    expect(screen.queryByText("inactive")).not.toBeInTheDocument();
    expect(screen.getByText("Manage Billing")).toBeInTheDocument();
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

  it("joins on click only, and keeps the button busy while it navigates away", async () => {
    const invitation = {
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
      signedInEmail: "editor@example.com",
      emailMatches: true,
    };
    const fetchMock = vi.fn().mockImplementation(async (_url: string, init?: RequestInit) =>
      init?.method === "POST"
        ? new Response(JSON.stringify({ ok: true }), { status: 200 })
        : new Response(JSON.stringify(invitation), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await act(async () => {
      render(
        <Suspense fallback={null}>
          <AcceptInvitePage params={Promise.resolve({ token: "sample-token-123456" })} />
        </Suspense>,
      );
    });

    const join = await screen.findByRole("button", { name: "Join Clip Crew" });
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
    fireEvent.click(join);

    await waitFor(() => expect(toast.success).toHaveBeenCalled());
    expect(fetchMock).toHaveBeenCalledWith("/api/teams/invitations/sample-token-123456/accept", { method: "POST" });
    expect(invalidateWorkspaces).toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /Joining team/ })).toBeDisabled();
  });

  it("says why a first load failed and loads again on Try again", async () => {
    let failing = true;
    vi.stubGlobal("fetch", vi.fn().mockImplementation(async () => failing
      ? new Response("{}", { status: 500 })
      : new Response(JSON.stringify({
        invitation: {
          teamId: "team-1", teamName: "Clip Crew", email: "editor@example.com", role: "member",
          status: "expired", inviterName: null, expiresAt: "2026-11-01T00:00:00.000Z",
        },
        signedIn: false, signedInEmail: null, emailMatches: false,
      }), { status: 200 })));

    await act(async () => {
      render(
        <Suspense fallback={null}>
          <AcceptInvitePage params={Promise.resolve({ token: "sample-token-123456" })} />
        </Suspense>,
      );
    });

    expect(await screen.findByText("Could not load this invitation.")).toBeInTheDocument();
    failing = false;
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText(/Clip Crew/)).toBeInTheDocument();
  });

  it("asks again after signing in instead of showing the signed-out answer from before", async () => {
    sessionState.user = null;
    const invitation = (signedIn: boolean) => ({
      invitation: {
        teamId: "team-1", teamName: "Clip Crew", email: "editor@example.com", role: "member",
        status: "valid", inviterName: "Alex", expiresAt: "2026-11-28T00:00:00.000Z",
      },
      signedIn,
      signedInEmail: signedIn ? "editor@example.com" : null,
      emailMatches: signedIn,
    });
    const fetchMock = vi.fn().mockImplementation(async () =>
      new Response(JSON.stringify(invitation(sessionState.user !== null)), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const params = Promise.resolve({ token: "sample-token-123456" });
    const page = () => (
      <Suspense fallback={null}>
        <AcceptInvitePage params={params} />
      </Suspense>
    );

    let view!: ReturnType<typeof render>;
    await act(async () => {
      view = render(page());
    });
    expect(await screen.findByRole("link", { name: /Create account/ })).toBeInTheDocument();

    // Back from /sign-in on the client: same page, same cache, now signed in.
    sessionState.user = { id: "u-editor", email: "editor@example.com", name: "Editor" };
    view.rerender(page());

    expect(await screen.findByRole("button", { name: "Join Clip Crew" })).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
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
