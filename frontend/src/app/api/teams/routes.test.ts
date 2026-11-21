// @vitest-environment node
import { beforeEach, describe, expect, it, vi } from "vitest";

const sessionMock = vi.fn();
const fetchBackendMock = vi.fn();
const getVerifiedActiveTeamIdMock = vi.fn();

const prismaMock = {
  team: {
    findUnique: vi.fn(),
    findUniqueOrThrow: vi.fn(),
    count: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
  },
  source: { deleteMany: vi.fn() },
  teamMember: {
    findMany: vi.fn(),
    findUnique: vi.fn(),
    findFirst: vi.fn(),
    count: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
  },
  teamInvitation: {
    findUnique: vi.fn(),
    findFirst: vi.fn(),
    count: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    updateMany: vi.fn(),
  },
  $queryRaw: vi.fn(),
  $transaction: vi.fn(),
};

const stripeTeamMocks = vi.hoisted(() => ({
  createTeamBillingPortalSession: vi.fn(),
  createTeamCheckoutSession: vi.fn(),
  updateTeamSeats: vi.fn(),
  teamHasBillableStripeSubscription: vi.fn(),
  withTeamBillingAccount: vi.fn(async (_teamId: string, fn: () => unknown) => fn()),
}));

vi.mock("next/headers", () => ({ cookies: vi.fn() }));
vi.mock("@/server/session", () => ({ getServerSession: () => sessionMock() }));
vi.mock("@/server/prisma", () => ({ getPrismaClient: () => prismaMock }));
vi.mock("@/server/backend-api", () => ({ fetchBackend: (...args: unknown[]) => fetchBackendMock(...args) }));
vi.mock("@/server/workspace", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/server/workspace")>();
  return {
    ...actual,
    getVerifiedActiveTeamId: (...args: unknown[]) => getVerifiedActiveTeamIdMock(...args),
  };
});
vi.mock("@/server/stripe-team-billing", () => stripeTeamMocks);
vi.mock("@/lib/monetization", () => ({ monetizationEnabled: true }));

import * as teamsRoute from "./route";
import * as activeRoute from "./active/route";
import * as teamDetailRoute from "./[teamId]/route";
import * as invitationsRoute from "./[teamId]/invitations/route";
import * as memberRoute from "./[teamId]/members/[memberUserId]/route";
import * as seatsRoute from "./[teamId]/seats/route";
import * as checkoutRoute from "./[teamId]/billing/checkout/route";
import * as portalRoute from "./[teamId]/billing/portal/route";
import * as inviteLookupRoute from "./invitations/[token]/route";
import * as inviteAcceptRoute from "./invitations/[token]/accept/route";

const jsonReq = (body: unknown, method = "POST") =>
  new Request("http://localhost:3107/api/teams", {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

const ctx = <T,>(params: T) => ({ params: Promise.resolve(params) });

describe("Next.js Team API routes", () => {
  beforeEach(() => {
    // Reset, not clear: a queued `mockResolvedValueOnce` must not leak into the next test.
    vi.resetAllMocks();
    vi.unstubAllEnvs();
    vi.stubEnv("STRIPE_TEAM_PRICE_ID", "price_team_month");
    prismaMock.$transaction.mockImplementation(async (fn: (tx: typeof prismaMock) => unknown) => fn(prismaMock));
    prismaMock.team.count.mockResolvedValue(0);
    stripeTeamMocks.withTeamBillingAccount.mockImplementation(
      async (_teamId: string, fn: () => unknown) => fn(),
    );
    stripeTeamMocks.teamHasBillableStripeSubscription.mockResolvedValue(false);
    sessionMock.mockResolvedValue({
      user: { id: "u-owner", email: "owner@example.com", name: "Owner" },
    });
  });

  it("rejects unauthenticated requests across team endpoints", async () => {
    sessionMock.mockResolvedValue(null);
    expect((await teamsRoute.GET()).status).toBe(401);
    expect((await teamsRoute.POST(jsonReq({ name: "Crew" }))).status).toBe(401);
    expect((await activeRoute.POST(jsonReq({ teamId: "t1" }))).status).toBe(401);
    expect((await teamDetailRoute.GET(new Request("http://localhost"), ctx({ teamId: "t1" }))).status).toBe(401);
    expect((await seatsRoute.POST(jsonReq({ seats: 5 }), ctx({ teamId: "t1" }))).status).toBe(401);
    expect((await inviteAcceptRoute.POST(new Request("http://localhost"), ctx({ token: "a".repeat(32) }))).status).toBe(401);
  });

  it("creates a team and sets the active workspace cookie", async () => {
    prismaMock.team.findUnique.mockResolvedValue(null);
    prismaMock.team.create.mockResolvedValue({
      id: "team-new",
      name: "Clip Squad",
      slug: "clip-squad",
      owner_id: "u-owner",
      plan: "free",
      subscription_status: "inactive",
      seat_limit: 3,
    });
    prismaMock.teamMember.create.mockResolvedValue({ id: "m1" });

    const res = await teamsRoute.POST(jsonReq({ name: "Clip Squad" }));
    expect(res.status).toBe(201);
    const body = await res.json();
    expect(body.team).toMatchObject({ id: "team-new", role: "owner", seatLimit: 3, occupiedSeats: 1 });
    expect(res.headers.get("set-cookie")).toContain("supoclip_team_id=team-new");
  });

  it("keeps team creation closed on a hosted install without Team prices", async () => {
    vi.stubEnv("STRIPE_TEAM_PRICE_ID", "");

    const res = await teamsRoute.POST(jsonReq({ name: "Clip Squad" }));
    expect(res.status).toBe(503);
    expect((await res.json()).code).toBe("teams_unavailable");
    expect(prismaMock.team.create).not.toHaveBeenCalled();
  });

  it("opens team creation when only the checkout account's Team price is set", async () => {
    // Two Stripe accounts with checkout on the destination: its prices are the
    // STRIPE_DESTINATION_* ones, and this route runs outside any account scope.
    vi.stubEnv("STRIPE_TEAM_PRICE_ID", "");
    vi.stubEnv("STRIPE_ACCOUNT_MIGRATION_ENABLED", "true");
    vi.stubEnv("STRIPE_SOURCE_ACCOUNT_ID", "acct_source1");
    vi.stubEnv("STRIPE_DESTINATION_ACCOUNT_ID", "acct_destination1");
    vi.stubEnv("STRIPE_CHECKOUT_ACCOUNT_ID", "acct_destination1");
    vi.stubEnv("STRIPE_DESTINATION_TEAM_PRICE_ID", "price_dest_team_month");
    prismaMock.team.findUnique.mockResolvedValue(null);
    prismaMock.team.create.mockResolvedValue({
      id: "team-new", name: "Clip Squad", slug: "clip-squad", owner_id: "u-owner",
      plan: "free", subscription_status: "inactive", seat_limit: 3,
    });
    prismaMock.teamMember.create.mockResolvedValue({ id: "m1" });
    prismaMock.teamMember.findMany.mockResolvedValue([]);
    getVerifiedActiveTeamIdMock.mockResolvedValue(null);

    expect((await (await teamsRoute.GET()).json()).available).toBe(true);
    expect((await teamsRoute.POST(jsonReq({ name: "Clip Squad" }))).status).toBe(201);
  });

  it("drops a workspace cookie the caller no longer has a membership for", async () => {
    const { cookies } = await import("next/headers");
    vi.mocked(cookies).mockResolvedValue({
      get: () => ({ name: "supoclip_team_id", value: "team-gone-1" }),
    } as never);
    prismaMock.teamMember.findMany.mockResolvedValue([]);
    getVerifiedActiveTeamIdMock.mockResolvedValue(null);

    const res = await teamsRoute.GET();
    expect(res.status).toBe(200);
    expect(await res.json()).toMatchObject({ activeTeamId: null, teams: [], available: true });
    expect(res.headers.get("set-cookie")).toContain("supoclip_team_id=;");
  });

  it("prevents switching into a team the user does not belong to and clears cookie on Personal switch", async () => {
    prismaMock.teamMember.findUnique.mockResolvedValueOnce(null);
    const denied = await activeRoute.POST(jsonReq({ teamId: "team-foreign" }));
    expect(denied.status).toBe(403);

    prismaMock.teamMember.findUnique.mockResolvedValueOnce({ team_id: "t1", role: "member" });
    const switched = await activeRoute.POST(jsonReq({ teamId: "t1" }));
    expect(switched.status).toBe(200);
    expect(switched.headers.get("set-cookie")).toContain("supoclip_team_id=t1");

    const personal = await activeRoute.POST(jsonReq({ teamId: null }));
    expect(personal.status).toBe(200);
    expect(personal.headers.get("set-cookie")).toContain("supoclip_team_id=;");
  });

  it("hides pending invitations from non-manager members on GET /api/teams/[teamId]", async () => {
    prismaMock.teamMember.findUnique.mockResolvedValue({ role: "member" });
    prismaMock.team.findUnique.mockResolvedValue({
      id: "t1",
      name: "Clip Squad",
      slug: "clip-squad",
      owner_id: "u-owner",
      plan: "team",
      subscription_status: "active",
      subscription_provider: "stripe",
      seat_limit: 5,
      billing_period_start: null,
      billing_period_end: null,
      subscription_cancel_at: null,
      stripe_customer_id: "cus_1",
      stripe_subscription_id: "sub_1",
      created_at: new Date(),
      members: [
        { user: { id: "u-owner", name: "Owner", email: "owner@example.com", image: null }, role: "owner", created_at: new Date() },
        { user: { id: "u-member", name: "Member", email: "member@example.com", image: null }, role: "member", created_at: new Date() },
      ],
      invitations: [
        { id: "inv-1", email: "secret@example.com", role: "member", inviter: null, expires_at: new Date(Date.now() + 86400000), created_at: new Date() },
      ],
    });
    fetchBackendMock.mockResolvedValue(new Response(JSON.stringify({ billing: {}, ai_edits: {} }), { status: 200 }));

    const res = await teamDetailRoute.GET(new Request("http://localhost"), ctx({ teamId: "t1" }));
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.permissions).toEqual({
      canManageMembers: false,
      canManageBilling: false,
      canDeleteTeam: false,
    });
    expect(data.seats).toEqual({ limit: 5, activeMembers: 2, pendingInvitations: 1, occupied: 3 });
    expect(data.invitations).toEqual([]);
  });

  it("blocks deleting a team with an active Stripe subscription and clears cookie when deleted", async () => {
    prismaMock.teamMember.findUnique.mockResolvedValue({ role: "owner" });
    prismaMock.team.findUnique.mockResolvedValueOnce({
      id: "t1",
      stripe_subscription_id: "sub_active",
      subscription_status: "active",
      subscription_cancel_at: null,
    });

    const blocked = await teamDetailRoute.DELETE(new Request("http://localhost", { method: "DELETE" }), ctx({ teamId: "t1" }));
    expect(blocked.status).toBe(409);
    expect((await blocked.json()).code).toBe("active_subscription");

    prismaMock.team.findUnique.mockResolvedValueOnce({
      id: "t1",
      stripe_subscription_id: null,
      subscription_status: "inactive",
      subscription_cancel_at: null,
    });
    fetchBackendMock.mockResolvedValue(new Response("{}", { status: 200 }));
    prismaMock.team.delete.mockResolvedValue({});

    // The row says nothing is subscribed, but Stripe still has a subscription that will charge.
    stripeTeamMocks.teamHasBillableStripeSubscription.mockResolvedValueOnce(true);
    const behind = await teamDetailRoute.DELETE(new Request("http://localhost", { method: "DELETE" }), ctx({ teamId: "t1" }));
    expect(behind.status).toBe(409);
    expect(prismaMock.team.delete).not.toHaveBeenCalled();

    prismaMock.team.findUnique.mockResolvedValueOnce({
      id: "t1",
      stripe_subscription_id: null,
      subscription_status: "inactive",
      subscription_cancel_at: null,
    });
    const deleted = await teamDetailRoute.DELETE(new Request("http://localhost", { method: "DELETE" }), ctx({ teamId: "t1" }));
    expect(deleted.status).toBe(200);
    expect(fetchBackendMock).toHaveBeenCalledWith(
      "/social/connections",
      expect.objectContaining({ method: "DELETE", teamId: "t1" }),
    );
    expect(deleted.headers.get("set-cookie")).toContain("supoclip_team_id=;");
  });

  it("creates invitation and returns inviteUrl even if SES email delivery fails", async () => {
    prismaMock.teamMember.findUnique.mockResolvedValue({ role: "owner" });
    prismaMock.team.findUnique.mockResolvedValue({
      id: "t1",
      name: "Clip Squad",
      seat_limit: 5,
      plan: "team",
      subscription_status: "active",
    });
    prismaMock.teamMember.findFirst.mockResolvedValue(null);
    prismaMock.teamInvitation.findFirst.mockResolvedValue(null);
    prismaMock.teamMember.count.mockResolvedValue(1);
    prismaMock.teamInvitation.count.mockResolvedValue(0);
    prismaMock.teamInvitation.create.mockImplementation(async ({ data }: { data: Record<string, unknown> }) => ({
      id: "inv-1",
      ...data,
      created_at: new Date(),
    }));
    fetchBackendMock.mockRejectedValueOnce(new Error("SES unavailable"));

    const res = await invitationsRoute.POST(
      jsonReq({ email: "editor@example.com", role: "member" }),
      ctx({ teamId: "t1" }),
    );
    expect(res.status).toBe(201);
    const body = await res.json();
    expect(body.emailSent).toBe(false);
    expect(body.inviteUrl).toContain("/invite/");
  });

  it("enforces owner-only billing & seat routes and clears cookie when a member leaves", async () => {
    // Admin is blocked from seat updates, checkout, and portal
    prismaMock.teamMember.findUnique.mockResolvedValue({ role: "admin" });
    expect((await seatsRoute.POST(jsonReq({ seats: 5 }), ctx({ teamId: "t1" }))).status).toBe(403);
    expect((await checkoutRoute.POST(jsonReq({ seats: 5, interval: "month" }), ctx({ teamId: "t1" }))).status).toBe(403);
    expect((await portalRoute.POST(new Request("http://localhost", { method: "POST" }), ctx({ teamId: "t1" }))).status).toBe(403);

    // Owner succeeds on seat update
    prismaMock.teamMember.findUnique.mockResolvedValue({ role: "owner" });
    stripeTeamMocks.updateTeamSeats.mockResolvedValue({ seatLimit: 5, prorationApplied: true });
    const seatRes = await seatsRoute.POST(jsonReq({ seats: 5 }), ctx({ teamId: "t1" }));
    expect(seatRes.status).toBe(200);
    expect(await seatRes.json()).toEqual({ seatLimit: 5, prorationApplied: true });

    // Member leaving team clears workspace cookie
    sessionMock.mockResolvedValue({ user: { id: "u-member", email: "member@example.com" } });
    prismaMock.teamMember.findUnique
      .mockResolvedValueOnce({ role: "member" })
      .mockResolvedValueOnce({ role: "member" });
    prismaMock.teamMember.delete.mockResolvedValue({});
    const leaveRes = await memberRoute.DELETE(
      new Request("http://localhost", { method: "DELETE" }),
      ctx({ teamId: "t1", memberUserId: "u-member" }),
    );
    expect(leaveRes.status).toBe(200);
    expect(leaveRes.headers.get("set-cookie")).toContain("supoclip_team_id=;");
  });

  it("looks up invitations publicly and sets workspace cookie upon acceptance", async () => {
    const token = "b".repeat(32);
    prismaMock.teamInvitation.findUnique.mockResolvedValueOnce({
      id: "inv-1",
      team_id: "t1",
      email: "editor@example.com",
      role: "member",
      accepted_at: null,
      revoked_at: null,
      expires_at: new Date(Date.now() + 60_000),
      team: { id: "t1", name: "Clip Squad" },
      inviter: { name: "Owner", email: "owner@example.com" },
    });
    sessionMock.mockResolvedValueOnce(null);

    const lookup = await inviteLookupRoute.GET(new Request("http://localhost"), ctx({ token }));
    expect(lookup.status).toBe(200);
    const lookupBody = await lookup.json();
    expect(lookupBody.signedIn).toBe(false);
    expect(lookupBody.invitation.status).toBe("valid");

    // Signed in with matching email accepts invite and switches active workspace cookie
    sessionMock.mockResolvedValueOnce({ user: { id: "u-editor", email: "Editor@Example.com" } });
    // Read once to find the team to lock, then again under the lock.
    prismaMock.teamInvitation.findUnique.mockResolvedValueOnce({ team_id: "t1" });
    prismaMock.teamInvitation.findUnique.mockResolvedValueOnce({
      id: "inv-1",
      team_id: "t1",
      email: "editor@example.com",
      role: "member",
      accepted_at: null,
      revoked_at: null,
      expires_at: new Date(Date.now() + 60_000),
      team: { id: "t1", name: "Clip Squad", seat_limit: 5 },
    });
    prismaMock.teamMember.findUnique.mockResolvedValueOnce(null);
    prismaMock.teamMember.count.mockResolvedValueOnce(1);
    prismaMock.team.findUniqueOrThrow.mockResolvedValueOnce({ id: "t1", name: "Clip Squad", seat_limit: 5 });
    prismaMock.teamMember.create.mockResolvedValueOnce({ id: "m2" });
    prismaMock.teamInvitation.update.mockResolvedValueOnce({ id: "inv-1" });

    const accepted = await inviteAcceptRoute.POST(new Request("http://localhost", { method: "POST" }), ctx({ token }));
    expect(accepted.status).toBe(200);
    expect(accepted.headers.get("set-cookie")).toContain("supoclip_team_id=t1");
  });

  it("updates and clears per-member generation limits via PATCH member route", async () => {
    // Reject empty payload
    const emptyRes = await memberRoute.PATCH(
      jsonReq({}),
      ctx({ teamId: "t1", memberUserId: "u-member" }),
    );
    expect(emptyRes.status).toBe(400);

    // Admin sets 600 min + 50 AI edits on a member
    prismaMock.teamMember.findUnique
      .mockResolvedValueOnce({ role: "admin" })
      .mockResolvedValueOnce({ role: "member" });
    prismaMock.teamMember.update.mockResolvedValueOnce({
      team_id: "t1",
      user_id: "u-member",
      role: "member",
      monthly_minute_limit: 600,
      monthly_ai_edit_limit: 50,
    });
    const setRes = await memberRoute.PATCH(
      jsonReq({ monthlyMinuteLimit: 600, monthlyAiEditLimit: 50 }),
      ctx({ teamId: "t1", memberUserId: "u-member" }),
    );
    expect(setRes.status).toBe(200);
    expect(await setRes.json()).toEqual({
      member: {
        teamId: "t1",
        userId: "u-member",
        role: "member",
        monthlyMinuteLimit: 600,
        monthlyAiEditLimit: 50,
      },
    });

    // Member cannot edit limits (403)
    prismaMock.teamMember.findUnique
      .mockResolvedValueOnce({ role: "member" })
      .mockResolvedValueOnce({ role: "member" });
    const forbiddenRes = await memberRoute.PATCH(
      jsonReq({ monthlyMinuteLimit: null }),
      ctx({ teamId: "t1", memberUserId: "u-member-2" }),
    );
    expect(forbiddenRes.status).toBe(403);
  });
});
