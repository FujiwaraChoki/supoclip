import { beforeEach, describe, expect, it, vi } from "vitest";

const prismaMock = {
  team: {
    findUnique: vi.fn(),
    findUniqueOrThrow: vi.fn(),
    count: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
  },
  teamMember: {
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

vi.mock("@/server/prisma", () => ({
  getPrismaClient: () => prismaMock,
}));

// Hosted mode: inviting needs a subscribed team.
vi.mock("@/lib/monetization", () => ({ monetizationEnabled: true }));

import {
  acceptTeamInvitation,
  canManageBilling,
  canManageMemberLimits,
  canManageMembers,
  canModifyMemberRole,
  canRemoveMember,
  createTeamForUser,
  createTeamInvitation,
  hashInvitationToken,
  lookupTeamInvitation,
  MAX_INVITATIONS_PER_DAY,
  MAX_OWNED_TEAMS,
  removeTeamMember,
  resendTeamInvitation,
  revokeTeamInvitation,
  slugifyTeamName,
  TeamError,
  transferTeamOwnership,
  updateTeamMemberLimits,
  updateTeamMemberRole,
  validateMemberLimit,
  validateTeamName,
} from "./teams";

const VALID_TOKEN = "a".repeat(32);
const SUBSCRIBED = { plan: "team", subscription_status: "active" };

describe("team permissions & helpers", () => {
  it("enforces role permission matrix", () => {
    expect(canManageMembers("owner")).toBe(true);
    expect(canManageMembers("admin")).toBe(true);
    expect(canManageMembers("member")).toBe(false);

    expect(canManageBilling("owner")).toBe(true);
    expect(canManageBilling("admin")).toBe(false);
    expect(canManageBilling("member")).toBe(false);

    expect(canModifyMemberRole("owner", "admin", "member")).toBe(true);
    expect(canModifyMemberRole("owner", "member", "admin")).toBe(true);
    expect(canModifyMemberRole("owner", "owner", "admin")).toBe(false);
    expect(canModifyMemberRole("admin", "member", "admin")).toBe(false);

    expect(canRemoveMember("u-owner", "owner", "u-admin", "admin")).toBe(true);
    expect(canRemoveMember("u-owner", "owner", "u-member", "member")).toBe(true);
    expect(canRemoveMember("u-owner", "owner", "u-owner", "owner")).toBe(false);
    expect(canRemoveMember("u-admin", "admin", "u-member", "member")).toBe(true);
    expect(canRemoveMember("u-admin", "admin", "u-admin-2", "admin")).toBe(false);
    expect(canRemoveMember("u-admin", "admin", "u-owner", "owner")).toBe(false);
    expect(canRemoveMember("u-admin", "admin", "u-admin", "admin")).toBe(true);
    expect(canRemoveMember("u-member", "member", "u-member", "member")).toBe(true);
    expect(canRemoveMember("u-member", "member", "u-member-2", "member")).toBe(false);
  });

  it("validates team names and generates clean slugs", () => {
    expect(validateTeamName("  Clip Crew  ")).toBe("Clip Crew");
    expect(() => validateTeamName("A")).toThrow(TeamError);
    expect(() => validateTeamName("a".repeat(81))).toThrow(TeamError);
    expect(slugifyTeamName("Apex Clipping Crew!")).toBe("apex-clipping-crew");
    expect(slugifyTeamName("   ")).toBe("team");
  });

  it("hashes invitation tokens deterministically with SHA-256", () => {
    const first = hashInvitationToken("sample-token");
    const second = hashInvitationToken("sample-token");
    expect(first).toBe(second);
    expect(first).toHaveLength(64);
    expect(first).not.toBe(hashInvitationToken("other-token"));
  });
});

describe("team lifecycle & seat cap enforcement", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    prismaMock.$transaction.mockImplementation(async (callback: (tx: typeof prismaMock) => unknown) =>
      callback(prismaMock),
    );
    prismaMock.team.count.mockResolvedValue(0);
  });

  it("stops one person from owning an unbounded number of teams", async () => {
    prismaMock.team.count.mockResolvedValue(MAX_OWNED_TEAMS);

    await expect(createTeamForUser("user-1", "One More")).rejects.toMatchObject({
      code: "team_limit_reached",
      status: 409,
    });
    expect(prismaMock.team.create).not.toHaveBeenCalled();
  });

  it("creates a team and assigns the creator as owner with included seats", async () => {
    prismaMock.team.findUnique.mockResolvedValue(null);
    prismaMock.team.create.mockResolvedValue({
      id: "team-1",
      name: "Demo Studio",
      slug: "demo-studio",
      owner_id: "user-1",
      seat_limit: 3,
    });
    prismaMock.teamMember.create.mockResolvedValue({ id: "mem-1" });

    const created = await createTeamForUser("user-1", "Demo Studio");

    expect(created.id).toBe("team-1");
    expect(prismaMock.team.create).toHaveBeenCalledWith(
      expect.objectContaining({
        data: expect.objectContaining({
          name: "Demo Studio",
          owner_id: "user-1",
          seat_limit: 3,
        }),
      }),
    );
    expect(prismaMock.teamMember.create).toHaveBeenCalledWith({
      data: { team_id: "team-1", user_id: "user-1", role: "owner" },
    });
  });

  it("blocks inviting when occupied seats reach seat_limit", async () => {
    prismaMock.teamMember.findUnique.mockResolvedValue({ role: "owner" });
    prismaMock.team.findUnique.mockResolvedValue({ id: "team-1", name: "Demo Studio", seat_limit: 3, ...SUBSCRIBED });
    prismaMock.teamMember.findFirst.mockResolvedValue(null);
    prismaMock.teamInvitation.findFirst.mockResolvedValue(null);
    prismaMock.teamMember.count.mockResolvedValue(2);
    prismaMock.teamInvitation.count.mockResolvedValue(1);

    await expect(
      createTeamInvitation({
        teamId: "team-1",
        inviterUserId: "user-1",
        rawEmail: "new@example.com",
        role: "member",
      }),
    ).rejects.toMatchObject({
      code: "seat_limit_reached",
      status: 409,
    });
  });

  it("sends no invitation email for a team without a subscription", async () => {
    prismaMock.teamMember.findUnique.mockResolvedValue({ role: "owner" });
    prismaMock.team.findUnique.mockResolvedValue({
      id: "team-1",
      name: "Demo Studio",
      seat_limit: 3,
      plan: "free",
      subscription_status: "inactive",
    });

    await expect(
      createTeamInvitation({
        teamId: "team-1",
        inviterUserId: "user-1",
        rawEmail: "new@example.com",
        role: "member",
      }),
    ).rejects.toMatchObject({ code: "subscription_required", status: 409 });
    expect(prismaMock.teamInvitation.create).not.toHaveBeenCalled();
  });

  it("caps the invitations a team can send in a day", async () => {
    prismaMock.teamMember.findUnique.mockResolvedValue({ role: "owner" });
    prismaMock.team.findUnique.mockResolvedValue({ id: "team-1", name: "Demo Studio", seat_limit: 100, ...SUBSCRIBED });
    prismaMock.teamInvitation.count.mockResolvedValue(MAX_INVITATIONS_PER_DAY);

    await expect(
      createTeamInvitation({
        teamId: "team-1",
        inviterUserId: "user-1",
        rawEmail: "new@example.com",
        role: "member",
      }),
    ).rejects.toMatchObject({ code: "invite_rate_limited", status: 429 });
    expect(prismaMock.teamInvitation.create).not.toHaveBeenCalled();
  });

  it("lets only the owner invite an admin", async () => {
    prismaMock.teamMember.findUnique.mockResolvedValue({ role: "admin" });

    await expect(
      createTeamInvitation({
        teamId: "team-1",
        inviterUserId: "user-admin",
        rawEmail: "new@example.com",
        role: "admin",
      }),
    ).rejects.toMatchObject({ code: "forbidden", status: 403 });
    expect(prismaMock.teamInvitation.create).not.toHaveBeenCalled();
  });

  it("does not resend an invitation that was sent a moment ago", async () => {
    prismaMock.teamMember.findUnique.mockResolvedValue({ role: "owner" });
    prismaMock.teamInvitation.findFirst.mockResolvedValue({
      id: "inv-1",
      team_id: "team-1",
      accepted_at: null,
      revoked_at: null,
      expires_at: new Date(Date.now() + 60_000),
      updated_at: new Date(),
      team: { id: "team-1", name: "Demo Studio", seat_limit: 5, ...SUBSCRIBED },
    });

    await expect(
      resendTeamInvitation({ teamId: "team-1", invitationId: "inv-1", actorUserId: "user-1" }),
    ).rejects.toMatchObject({ code: "invite_rate_limited", status: 429 });
    expect(prismaMock.teamInvitation.update).not.toHaveBeenCalled();
  });

  it("blocks regular members from sending invitations", async () => {
    prismaMock.teamMember.findUnique.mockResolvedValue({ role: "member" });

    await expect(
      createTeamInvitation({
        teamId: "team-1",
        inviterUserId: "user-2",
        rawEmail: "new@example.com",
        role: "member",
      }),
    ).rejects.toMatchObject({
      code: "forbidden",
      status: 403,
    });
  });

  it("creates an invitation with a hashed token when a seat is available", async () => {
    prismaMock.teamMember.findUnique.mockResolvedValue({ role: "admin" });
    prismaMock.team.findUnique.mockResolvedValue({ id: "team-1", name: "Demo Studio", seat_limit: 5, ...SUBSCRIBED });
    prismaMock.teamMember.findFirst.mockResolvedValue(null);
    prismaMock.teamInvitation.findFirst.mockResolvedValue(null);
    prismaMock.teamMember.count.mockResolvedValue(2);
    prismaMock.teamInvitation.count.mockResolvedValue(1);
    prismaMock.teamInvitation.create.mockImplementation(async ({ data }: { data: Record<string, unknown> }) => ({
      id: "inv-1",
      ...data,
      created_at: new Date(),
    }));

    const result = await createTeamInvitation({
      teamId: "team-1",
      inviterUserId: "user-admin",
      rawEmail: "Editor@Example.com ",
      role: "member",
    });

    expect(result.rawToken).toBeTruthy();
    expect(result.invitation.email).toBe("editor@example.com");
    expect(result.invitation.token_hash).toBe(hashInvitationToken(result.rawToken));
  });

  it("classifies invitation lookup states (valid, expired, revoked, accepted)", async () => {
    const baseInvite = {
      id: "inv-1",
      team_id: "team-1",
      email: "editor@example.com",
      role: "member",
      accepted_at: null,
      revoked_at: null,
      expires_at: new Date(Date.now() + 60_000),
      team: { id: "team-1", name: "Demo Studio" },
      inviter: { name: "Owner", email: "owner@example.com" },
    };

    prismaMock.teamInvitation.findUnique.mockResolvedValueOnce(baseInvite);
    expect((await lookupTeamInvitation(VALID_TOKEN))?.status).toBe("valid");

    prismaMock.teamInvitation.findUnique.mockResolvedValueOnce({
      ...baseInvite,
      expires_at: new Date(Date.now() - 60_000),
    });
    expect((await lookupTeamInvitation(VALID_TOKEN))?.status).toBe("expired");

    prismaMock.teamInvitation.findUnique.mockResolvedValueOnce({
      ...baseInvite,
      revoked_at: new Date(),
    });
    expect((await lookupTeamInvitation(VALID_TOKEN))?.status).toBe("revoked");

    prismaMock.teamInvitation.findUnique.mockResolvedValueOnce({
      ...baseInvite,
      accepted_at: new Date(),
    });
    expect((await lookupTeamInvitation(VALID_TOKEN))?.status).toBe("accepted");
  });

  it("rejects accepting an invitation when signed-in email does not match", async () => {
    prismaMock.teamInvitation.findUnique.mockResolvedValue({
      id: "inv-1",
      team_id: "team-1",
      email: "invited@example.com",
      role: "member",
      accepted_at: null,
      revoked_at: null,
      expires_at: new Date(Date.now() + 60_000),
      team: { id: "team-1", name: "Demo Studio", seat_limit: 5 },
    });

    await expect(
      acceptTeamInvitation({
        rawToken: VALID_TOKEN,
        userId: "user-other",
        userEmail: "someone-else@example.com",
      }),
    ).rejects.toMatchObject({
      code: "email_mismatch",
      status: 403,
    });
  });

  it("accepts a valid invitation and adds the member", async () => {
    prismaMock.teamInvitation.findUnique.mockResolvedValue({
      id: "inv-1",
      team_id: "team-1",
      email: "editor@example.com",
      role: "admin",
      accepted_at: null,
      revoked_at: null,
      expires_at: new Date(Date.now() + 60_000),
      team: { id: "team-1", name: "Demo Studio", seat_limit: 5 },
    });
    prismaMock.teamMember.findUnique.mockResolvedValue(null);
    prismaMock.teamMember.count.mockResolvedValue(2);
    prismaMock.team.findUniqueOrThrow.mockResolvedValue({
      id: "team-1",
      name: "Demo Studio",
      seat_limit: 5,
    });
    prismaMock.teamMember.create.mockResolvedValue({ id: "mem-2" });
    prismaMock.teamInvitation.update.mockResolvedValue({ id: "inv-1" });

    const accepted = await acceptTeamInvitation({
      rawToken: VALID_TOKEN,
      userId: "user-editor",
      userEmail: "Editor@Example.com",
    });

    expect(accepted).toEqual({ teamId: "team-1", teamName: "Demo Studio", role: "admin" });
    expect(prismaMock.teamMember.create).toHaveBeenCalledWith({
      data: { team_id: "team-1", user_id: "user-editor", role: "admin" },
    });
  });

  it("enforces role permissions on revoke, role change, removal, and ownership transfer", async () => {
    // Revoke requires manager
    prismaMock.teamMember.findUnique.mockResolvedValueOnce({ role: "member" });
    await expect(
      revokeTeamInvitation({ teamId: "team-1", invitationId: "inv-1", actorUserId: "user-member" }),
    ).rejects.toMatchObject({
      code: "forbidden",
      status: 403,
    });

    // Only owner can promote member to admin
    prismaMock.teamMember.findUnique
      .mockResolvedValueOnce({ role: "admin" })
      .mockResolvedValueOnce({ role: "member" });
    await expect(
      updateTeamMemberRole({
        teamId: "team-1",
        actorUserId: "user-admin",
        targetUserId: "user-member",
        newRole: "admin",
      }),
    ).rejects.toMatchObject({ code: "forbidden", status: 403 });

    // Owner cannot leave without transferring
    prismaMock.teamMember.findUnique
      .mockResolvedValueOnce({ role: "owner" })
      .mockResolvedValueOnce({ role: "owner" });
    await expect(
      removeTeamMember({
        teamId: "team-1",
        actorUserId: "user-owner",
        targetUserId: "user-owner",
      }),
    ).rejects.toMatchObject({ status: 403 });

    // Ownership transfer demotes previous owner to admin and promotes target to owner
    prismaMock.teamMember.findUnique
      .mockResolvedValueOnce({ role: "owner" })
      .mockResolvedValueOnce({ role: "admin" });
    prismaMock.team.update.mockResolvedValue({ id: "team-1", owner_id: "user-admin" });
    prismaMock.teamMember.update.mockResolvedValue({});

    await transferTeamOwnership({
      teamId: "team-1",
      actorUserId: "user-owner",
      newOwnerUserId: "user-admin",
    });
    expect(prismaMock.team.update).toHaveBeenCalledWith({
      where: { id: "team-1" },
      data: { owner_id: "user-admin" },
    });
    expect(prismaMock.teamMember.update).toHaveBeenCalledWith({
      where: { team_id_user_id: { team_id: "team-1", user_id: "user-admin" } },
      data: {
        role: "owner",
        monthly_minute_limit: null,
        monthly_ai_edit_limit: null,
      },
    });
  });

  it("validates and enforces RBAC for per-member generation limits", async () => {
    expect(canManageMemberLimits("owner", "admin")).toBe(true);
    expect(canManageMemberLimits("owner", "member")).toBe(true);
    expect(canManageMemberLimits("owner", "owner")).toBe(false);
    expect(canManageMemberLimits("admin", "member")).toBe(true);
    expect(canManageMemberLimits("admin", "admin")).toBe(false);
    expect(canManageMemberLimits("admin", "owner")).toBe(false);
    expect(canManageMemberLimits("member", "member")).toBe(false);

    expect(validateMemberLimit(null, "Video", 1000)).toBeNull();
    expect(validateMemberLimit("", "Video", 1000)).toBeNull();
    expect(validateMemberLimit(0, "Video", 1000)).toBe(0);
    expect(validateMemberLimit(600, "Video", 1000)).toBe(600);
    expect(() => validateMemberLimit(-1, "Video", 1000)).toThrow();
    expect(() => validateMemberLimit(12.5, "Video", 1000)).toThrow();
    expect(() => validateMemberLimit(1001, "Video", 1000)).toThrow();

    // Admin can cap a regular member
    prismaMock.teamMember.findUnique
      .mockResolvedValueOnce({ role: "admin" })
      .mockResolvedValueOnce({ role: "member" });
    prismaMock.teamMember.update.mockResolvedValueOnce({
      team_id: "team-1",
      user_id: "user-member",
      role: "member",
      monthly_minute_limit: 300,
      monthly_ai_edit_limit: 25,
    });
    const updated = await updateTeamMemberLimits({
      teamId: "team-1",
      actorUserId: "user-admin",
      targetUserId: "user-member",
      monthlyMinuteLimit: 300,
      monthlyAiEditLimit: 25,
    });
    expect(updated.monthly_minute_limit).toBe(300);
    expect(updated.monthly_ai_edit_limit).toBe(25);

    // Admin cannot cap another admin or the owner
    prismaMock.teamMember.findUnique
      .mockResolvedValueOnce({ role: "admin" })
      .mockResolvedValueOnce({ role: "admin" });
    await expect(
      updateTeamMemberLimits({
        teamId: "team-1",
        actorUserId: "user-admin",
        targetUserId: "user-admin-2",
        monthlyMinuteLimit: 120,
      }),
    ).rejects.toMatchObject({ code: "forbidden", status: 403 });

    // Owner cannot cap themselves (owner role)
    prismaMock.teamMember.findUnique
      .mockResolvedValueOnce({ role: "owner" })
      .mockResolvedValueOnce({ role: "owner" });
    await expect(
      updateTeamMemberLimits({
        teamId: "team-1",
        actorUserId: "user-owner",
        targetUserId: "user-owner",
        monthlyMinuteLimit: 120,
      }),
    ).rejects.toMatchObject({ code: "forbidden", status: 403 });
  });
});
