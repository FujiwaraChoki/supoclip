import crypto from "crypto";
import type { Prisma, PrismaClient } from "@/generated/prisma";
import { getTeamBillingConfig } from "@/lib/billing-plans";
import { monetizationEnabled } from "@/lib/monetization";
import { getPrismaClient } from "@/server/prisma";

export type TeamRole = "owner" | "admin" | "member";
export type InvitableTeamRole = "admin" | "member";

export const INVITATION_TTL_DAYS = 7;
export const TEAM_ROLES: readonly TeamRole[] = ["owner", "admin", "member"];
export const INVITABLE_ROLES: readonly InvitableTeamRole[] = ["admin", "member"];
export const MAX_MEMBER_MINUTE_LIMIT = 100_000;
export const MAX_MEMBER_AI_EDIT_LIMIT = 10_000;
/** Teams one person may own. Creating a team is free, so it is bounded. */
export const MAX_OWNED_TEAMS = 10;
/** Invitation emails a team may send per day, counting ones since revoked. */
export const MAX_INVITATIONS_PER_DAY = 50;
/** Wait between two emails for the same invitation. */
export const INVITATION_RESEND_COOLDOWN_MS = 60_000;

const INVITING_SUBSCRIPTION_STATUSES = new Set(["active", "trialing", "past_due"]);

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export class TeamError extends Error {
  status: number;
  code: string;
  details?: Record<string, unknown>;

  constructor(
    message: string,
    status = 400,
    code = "team_error",
    details?: Record<string, unknown>,
  ) {
    super(message);
    this.name = "TeamError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export function isTeamRole(value: unknown): value is TeamRole {
  return value === "owner" || value === "admin" || value === "member";
}

export function isInvitableTeamRole(value: unknown): value is InvitableTeamRole {
  return value === "admin" || value === "member";
}

export function canManageMembers(role: string | null | undefined): boolean {
  return role === "owner" || role === "admin";
}

export function canManageBilling(role: string | null | undefined): boolean {
  return role === "owner";
}

/** Owners invite admins and members; admins only members (as with role changes). */
export function canInviteRole(
  actorRole: string | null | undefined,
  role: InvitableTeamRole,
): boolean {
  if (actorRole === "owner") return true;
  return actorRole === "admin" && role === "member";
}

/**
 * Invitation emails carry the team's name to an address the inviter picks. On
 * a hosted install only a subscribed team may send them: otherwise any free
 * account could mail arbitrary text to arbitrary people from our domain.
 */
function assertTeamMayInvite(team: { plan: string; subscription_status: string }): void {
  if (!monetizationEnabled) return;
  if (team.plan === "team" && INVITING_SUBSCRIPTION_STATUSES.has(team.subscription_status)) {
    return;
  }
  throw new TeamError(
    "Start the Team subscription before inviting teammates.",
    409,
    "subscription_required",
  );
}

export function canModifyMemberRole(
  actorRole: TeamRole,
  targetRole: TeamRole,
  newRole: InvitableTeamRole,
): boolean {
  if (targetRole === "owner") return false;
  if (actorRole === "owner") return true;
  if (actorRole === "admin") {
    return targetRole === "member" && newRole === "member";
  }
  return false;
}

export function canManageMemberLimits(
  actorRole: TeamRole,
  targetRole: TeamRole,
): boolean {
  if (targetRole === "owner") return false;
  if (actorRole === "owner") return true;
  if (actorRole === "admin") return targetRole === "member";
  return false;
}

export function validateMemberLimit(
  raw: unknown,
  label: string,
  max: number,
): number | null {
  if (raw === null || raw === "") return null;
  if (typeof raw !== "number" || !Number.isInteger(raw) || raw < 0 || raw > max) {
    throw new TeamError(
      `${label} must be an integer between 0 and ${max.toLocaleString()} (or empty for unlimited)`,
      400,
      "invalid_limit",
    );
  }
  return raw;
}

export function canRemoveMember(
  actorUserId: string,
  actorRole: TeamRole,
  targetUserId: string,
  targetRole: TeamRole,
): boolean {
  if (actorUserId === targetUserId) {
    // Owners must transfer ownership or delete the team instead of leaving it orphaned.
    return actorRole !== "owner";
  }
  if (targetRole === "owner") return false;
  if (actorRole === "owner") return true;
  if (actorRole === "admin") return targetRole === "member";
  return false;
}

export function normalizeEmail(email: string): string {
  return email.trim().toLowerCase();
}

export function validateTeamName(raw: unknown): string {
  if (typeof raw !== "string") {
    throw new TeamError("Team name is required", 400, "invalid_name");
  }
  const cleaned = raw.trim().replace(/\s+/g, " ");
  if (cleaned.length < 2 || cleaned.length > 80) {
    throw new TeamError(
      "Team name must be between 2 and 80 characters",
      400,
      "invalid_name",
    );
  }
  return cleaned;
}

export function slugifyTeamName(name: string): string {
  const base = name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 48);
  return base || "team";
}

export function hashInvitationToken(rawToken: string): string {
  return crypto.createHash("sha256").update(rawToken.trim()).digest("hex");
}

export function generateInvitationToken(): { rawToken: string; tokenHash: string } {
  const rawToken = crypto.randomBytes(32).toString("base64url");
  return { rawToken, tokenHash: hashInvitationToken(rawToken) };
}

export async function generateUniqueTeamSlug(
  db: PrismaClient | Prisma.TransactionClient,
  name: string,
): Promise<string> {
  const base = slugifyTeamName(name);
  for (let attempt = 0; attempt < 8; attempt += 1) {
    const suffix = attempt === 0 ? "" : `-${crypto.randomBytes(3).toString("hex")}`;
    const candidate = `${base}${suffix}`.slice(0, 80);
    const existing = await db.team.findUnique({
      where: { slug: candidate },
      select: { id: true },
    });
    if (!existing) return candidate;
  }
  return `${base}-${crypto.randomUUID().slice(0, 8)}`.slice(0, 80);
}

export async function countOccupiedSeats(
  db: PrismaClient | Prisma.TransactionClient,
  teamId: string,
  now: Date = new Date(),
): Promise<{ activeMembers: number; pendingInvitations: number; occupiedSeats: number }> {
  const [activeMembers, pendingInvitations] = await Promise.all([
    db.teamMember.count({ where: { team_id: teamId } }),
    db.teamInvitation.count({
      where: {
        team_id: teamId,
        accepted_at: null,
        revoked_at: null,
        expires_at: { gt: now },
      },
    }),
  ]);
  return {
    activeMembers,
    pendingInvitations,
    occupiedSeats: activeMembers + pendingInvitations,
  };
}

export async function createTeamForUser(
  userId: string,
  rawName: unknown,
  db: PrismaClient = getPrismaClient(),
) {
  const name = validateTeamName(rawName);
  const includedSeats = getTeamBillingConfig().includedSeats;

  return db.$transaction(async (tx) => {
    const owned = await tx.team.count({ where: { owner_id: userId } });
    if (owned >= MAX_OWNED_TEAMS) {
      throw new TeamError(
        `You can own up to ${MAX_OWNED_TEAMS} teams. Delete one you no longer use first.`,
        409,
        "team_limit_reached",
      );
    }
    const slug = await generateUniqueTeamSlug(tx, name);
    const team = await tx.team.create({
      data: {
        name,
        slug,
        owner_id: userId,
        plan: "free",
        subscription_status: "inactive",
        seat_limit: includedSeats,
      },
    });
    await tx.teamMember.create({
      data: {
        team_id: team.id,
        user_id: userId,
        role: "owner",
      },
    });
    return team;
  });
}

export async function createTeamInvitation(
  {
    teamId,
    inviterUserId,
    rawEmail,
    role,
  }: {
    teamId: string;
    inviterUserId: string;
    rawEmail: unknown;
    role: unknown;
  },
  db: PrismaClient = getPrismaClient(),
) {
  if (typeof rawEmail !== "string" || !EMAIL_RE.test(rawEmail.trim())) {
    throw new TeamError("Enter a valid email address", 400, "invalid_email");
  }
  if (!isInvitableTeamRole(role)) {
    throw new TeamError("Role must be admin or member", 400, "invalid_role");
  }
  const email = normalizeEmail(rawEmail);
  const now = new Date();
  const expiresAt = new Date(now.getTime() + INVITATION_TTL_DAYS * 24 * 60 * 60 * 1000);
  const { rawToken, tokenHash } = generateInvitationToken();

  const result = await db.$transaction(async (tx) => {
    // Serialize invitations and seat changes on this team row.
    await tx.$queryRaw`SELECT id FROM teams WHERE id = ${teamId} FOR UPDATE`;

    const actor = await tx.teamMember.findUnique({
      where: { team_id_user_id: { team_id: teamId, user_id: inviterUserId } },
      select: { role: true },
    });
    if (!actor || !canManageMembers(actor.role)) {
      throw new TeamError(
        "Only team owners and admins can invite members",
        403,
        "forbidden",
      );
    }
    if (!canInviteRole(actor.role, role)) {
      throw new TeamError("Only the team owner can invite an admin", 403, "forbidden");
    }

    const team = await tx.team.findUnique({
      where: { id: teamId },
      select: { id: true, name: true, seat_limit: true, plan: true, subscription_status: true },
    });
    if (!team) {
      throw new TeamError("Team not found", 404, "team_not_found");
    }
    assertTeamMayInvite(team);

    const sentToday = await tx.teamInvitation.count({
      where: {
        team_id: teamId,
        created_at: { gt: new Date(now.getTime() - 24 * 60 * 60 * 1000) },
      },
    });
    if (sentToday >= MAX_INVITATIONS_PER_DAY) {
      throw new TeamError(
        "This team has sent too many invitations today. Try again tomorrow.",
        429,
        "invite_rate_limited",
      );
    }

    // Check if the email already belongs to a member of this team.
    const existingMember = await tx.teamMember.findFirst({
      where: {
        team_id: teamId,
        user: { email: { equals: email, mode: "insensitive" } },
      },
      select: { user_id: true },
    });
    if (existingMember) {
      throw new TeamError(
        "That person is already a member of this team",
        409,
        "already_member",
      );
    }

    // Check if an active pending invitation already exists for this email.
    const existingInvite = await tx.teamInvitation.findFirst({
      where: {
        team_id: teamId,
        email: { equals: email, mode: "insensitive" },
        accepted_at: null,
        revoked_at: null,
        expires_at: { gt: now },
      },
      select: { id: true },
    });
    if (existingInvite) {
      throw new TeamError(
        "An active invitation has already been sent to that email. Resend or revoke it instead.",
        409,
        "already_invited",
      );
    }

    const seats = await countOccupiedSeats(tx, teamId, now);
    if (seats.occupiedSeats >= team.seat_limit) {
      throw new TeamError(
        `All ${team.seat_limit} seats are currently in use. Add more seats to invite another teammate.`,
        409,
        "seat_limit_reached",
        {
          seatLimit: team.seat_limit,
          occupiedSeats: seats.occupiedSeats,
          activeMembers: seats.activeMembers,
          pendingInvitations: seats.pendingInvitations,
        },
      );
    }

    const invitation = await tx.teamInvitation.create({
      data: {
        team_id: teamId,
        email,
        role,
        token_hash: tokenHash,
        invited_by: inviterUserId,
        expires_at: expiresAt,
      },
    });

    return { invitation, team };
  });

  return { ...result, rawToken };
}

export async function resendTeamInvitation(
  {
    teamId,
    invitationId,
    actorUserId,
  }: {
    teamId: string;
    invitationId: string;
    actorUserId: string;
  },
  db: PrismaClient = getPrismaClient(),
) {
  const now = new Date();
  const expiresAt = new Date(now.getTime() + INVITATION_TTL_DAYS * 24 * 60 * 60 * 1000);
  const { rawToken, tokenHash } = generateInvitationToken();

  const result = await db.$transaction(async (tx) => {
    await tx.$queryRaw`SELECT id FROM teams WHERE id = ${teamId} FOR UPDATE`;

    const actor = await tx.teamMember.findUnique({
      where: { team_id_user_id: { team_id: teamId, user_id: actorUserId } },
      select: { role: true },
    });
    if (!actor || !canManageMembers(actor.role)) {
      throw new TeamError(
        "Only team owners and admins can resend invitations",
        403,
        "forbidden",
      );
    }

    const invitation = await tx.teamInvitation.findFirst({
      where: { id: invitationId, team_id: teamId },
      include: {
        team: {
          select: { id: true, name: true, seat_limit: true, plan: true, subscription_status: true },
        },
      },
    });
    if (!invitation || invitation.accepted_at || invitation.revoked_at) {
      throw new TeamError("Invitation not found", 404, "invitation_not_found");
    }
    assertTeamMayInvite(invitation.team);
    if (now.getTime() - invitation.updated_at.getTime() < INVITATION_RESEND_COOLDOWN_MS) {
      throw new TeamError(
        "This invitation was just sent. Wait a minute before sending it again.",
        429,
        "invite_rate_limited",
      );
    }

    // If the old invitation had expired, re-check seat capacity before reviving it.
    if (invitation.expires_at <= now) {
      const seats = await countOccupiedSeats(tx, teamId, now);
      if (seats.occupiedSeats >= invitation.team.seat_limit) {
        throw new TeamError(
          `All ${invitation.team.seat_limit} seats are currently in use. Add more seats first.`,
          409,
          "seat_limit_reached",
        );
      }
    }

    const updated = await tx.teamInvitation.update({
      where: { id: invitation.id },
      data: {
        token_hash: tokenHash,
        expires_at: expiresAt,
        invited_by: actorUserId,
      },
    });
    return { invitation: updated, team: invitation.team };
  });

  return { ...result, rawToken };
}

export async function revokeTeamInvitation(
  {
    teamId,
    invitationId,
    actorUserId,
  }: {
    teamId: string;
    invitationId: string;
    actorUserId: string;
  },
  db: PrismaClient = getPrismaClient(),
) {
  await db.$transaction(async (tx) => {
    await tx.$queryRaw`SELECT id FROM teams WHERE id = ${teamId} FOR UPDATE`;
    const actor = await tx.teamMember.findUnique({
      where: { team_id_user_id: { team_id: teamId, user_id: actorUserId } },
      select: { role: true },
    });
    if (!actor || !canManageMembers(actor.role)) {
      throw new TeamError(
        "Only team owners and admins can revoke invitations",
        403,
        "forbidden",
      );
    }

    const updated = await tx.teamInvitation.updateMany({
      where: {
        id: invitationId,
        team_id: teamId,
        accepted_at: null,
        revoked_at: null,
      },
      data: { revoked_at: new Date() },
    });
    if (updated.count === 0) {
      throw new TeamError("Invitation not found", 404, "invitation_not_found");
    }
  });
}

export type InvitationLookupResult = {
  status: "valid" | "expired" | "revoked" | "accepted";
  teamId: string;
  teamName: string;
  role: string;
  email: string;
  inviterName: string | null;
  expiresAt: string;
};

export async function lookupTeamInvitation(
  rawToken: string,
  db: PrismaClient = getPrismaClient(),
): Promise<InvitationLookupResult | null> {
  if (!rawToken || rawToken.trim().length < 16) return null;
  const tokenHash = hashInvitationToken(rawToken);
  const invitation = await db.teamInvitation.findUnique({
    where: { token_hash: tokenHash },
    include: {
      team: { select: { id: true, name: true } },
      inviter: { select: { name: true, email: true } },
    },
  });
  if (!invitation) return null;

  let status: InvitationLookupResult["status"] = "valid";
  if (invitation.accepted_at) status = "accepted";
  else if (invitation.revoked_at) status = "revoked";
  else if (invitation.expires_at <= new Date()) status = "expired";

  return {
    status,
    teamId: invitation.team.id,
    teamName: invitation.team.name,
    role: invitation.role,
    email: invitation.email,
    inviterName: invitation.inviter?.name || invitation.inviter?.email || null,
    expiresAt: invitation.expires_at.toISOString(),
  };
}

export async function acceptTeamInvitation(
  {
    rawToken,
    userId,
    userEmail,
  }: {
    rawToken: string;
    userId: string;
    userEmail: string;
  },
  db: PrismaClient = getPrismaClient(),
) {
  if (!rawToken || rawToken.trim().length < 16) {
    throw new TeamError("Invalid invitation link", 404, "invitation_not_found");
  }
  const tokenHash = hashInvitationToken(rawToken);
  const now = new Date();

  return db.$transaction(async (tx) => {
    const found = await tx.teamInvitation.findUnique({
      where: { token_hash: tokenHash },
      select: { team_id: true },
    });
    if (!found) {
      throw new TeamError("Invitation not found", 404, "invitation_not_found");
    }

    // Take the team lock first, then read the invitation: a revoke, a resend
    // (new token) or another accept that ran meanwhile is seen here.
    await tx.$queryRaw`SELECT id FROM teams WHERE id = ${found.team_id} FOR UPDATE`;
    const invitation = await tx.teamInvitation.findUnique({
      where: { token_hash: tokenHash },
      include: { team: { select: { id: true, name: true, seat_limit: true } } },
    });
    if (!invitation) {
      throw new TeamError("Invitation not found", 404, "invitation_not_found");
    }

    if (invitation.revoked_at) {
      throw new TeamError("This invitation was revoked", 410, "invitation_revoked");
    }
    if (invitation.accepted_at) {
      throw new TeamError(
        "This invitation has already been accepted",
        409,
        "invitation_accepted",
      );
    }
    if (invitation.expires_at <= now) {
      throw new TeamError(
        "This invitation has expired. Ask a team admin for a new invite.",
        410,
        "invitation_expired",
      );
    }
    if (normalizeEmail(userEmail) !== normalizeEmail(invitation.email)) {
      throw new TeamError(
        `This invitation was sent to ${invitation.email}. Sign in with that address to accept it.`,
        403,
        "email_mismatch",
        { invitedEmail: invitation.email },
      );
    }

    const existingMembership = await tx.teamMember.findUnique({
      where: {
        team_id_user_id: { team_id: invitation.team_id, user_id: userId },
      },
    });
    if (existingMembership) {
      await tx.teamInvitation.update({
        where: { id: invitation.id },
        data: { accepted_at: now },
      });
      return { teamId: invitation.team.id, teamName: invitation.team.name, role: existingMembership.role };
    }

    const activeMembers = await tx.teamMember.count({
      where: { team_id: invitation.team_id },
    });
    const team = await tx.team.findUniqueOrThrow({
      where: { id: invitation.team_id },
      select: { id: true, name: true, seat_limit: true },
    });
    if (activeMembers >= team.seat_limit) {
      throw new TeamError(
        `This team has reached its ${team.seat_limit}-seat limit. Ask the team owner to add more seats.`,
        409,
        "seat_limit_reached",
      );
    }

    await tx.teamMember.create({
      data: {
        team_id: invitation.team_id,
        user_id: userId,
        role: isInvitableTeamRole(invitation.role) ? invitation.role : "member",
      },
    });
    await tx.teamInvitation.update({
      where: { id: invitation.id },
      data: { accepted_at: now },
    });

    return {
      teamId: team.id,
      teamName: team.name,
      role: invitation.role,
    };
  });
}

export async function updateTeamMemberRole(
  {
    teamId,
    actorUserId,
    targetUserId,
    newRole,
  }: {
    teamId: string;
    actorUserId: string;
    targetUserId: string;
    newRole: unknown;
  },
  db: PrismaClient = getPrismaClient(),
) {
  if (!isInvitableTeamRole(newRole)) {
    throw new TeamError("Role must be admin or member", 400, "invalid_role");
  }
  // Roles are read and written under the team lock, so a change cannot run
  // against the roles from before a concurrent ownership transfer.
  return db.$transaction(async (tx) => {
    await tx.$queryRaw`SELECT id FROM teams WHERE id = ${teamId} FOR UPDATE`;
    const [actor, target] = await Promise.all([
      tx.teamMember.findUnique({
        where: { team_id_user_id: { team_id: teamId, user_id: actorUserId } },
      }),
      tx.teamMember.findUnique({
        where: { team_id_user_id: { team_id: teamId, user_id: targetUserId } },
      }),
    ]);
    if (!actor || !isTeamRole(actor.role)) {
      throw new TeamError("Not a member of this team", 403, "forbidden");
    }
    if (!target || !isTeamRole(target.role)) {
      throw new TeamError("Member not found", 404, "member_not_found");
    }
    if (!canModifyMemberRole(actor.role, target.role, newRole)) {
      throw new TeamError(
        "You don't have permission to change this member's role",
        403,
        "forbidden",
      );
    }
    return tx.teamMember.update({
      where: { team_id_user_id: { team_id: teamId, user_id: targetUserId } },
      data: { role: newRole },
    });
  });
}

export async function updateTeamMemberLimits(
  {
    teamId,
    actorUserId,
    targetUserId,
    monthlyMinuteLimit,
    monthlyAiEditLimit,
  }: {
    teamId: string;
    actorUserId: string;
    targetUserId: string;
    monthlyMinuteLimit?: unknown;
    monthlyAiEditLimit?: unknown;
  },
  db: PrismaClient = getPrismaClient(),
) {
  const data: {
    monthly_minute_limit?: number | null;
    monthly_ai_edit_limit?: number | null;
  } = {};

  if (monthlyMinuteLimit !== undefined) {
    data.monthly_minute_limit = validateMemberLimit(
      monthlyMinuteLimit,
      "Monthly video minute limit",
      MAX_MEMBER_MINUTE_LIMIT,
    );
  }
  if (monthlyAiEditLimit !== undefined) {
    data.monthly_ai_edit_limit = validateMemberLimit(
      monthlyAiEditLimit,
      "Monthly AI edit limit",
      MAX_MEMBER_AI_EDIT_LIMIT,
    );
  }
  if (Object.keys(data).length === 0) {
    throw new TeamError("Provide at least one limit to update", 400, "invalid_limit");
  }

  const [actor, target] = await Promise.all([
    db.teamMember.findUnique({
      where: { team_id_user_id: { team_id: teamId, user_id: actorUserId } },
    }),
    db.teamMember.findUnique({
      where: { team_id_user_id: { team_id: teamId, user_id: targetUserId } },
    }),
  ]);
  if (!actor || !isTeamRole(actor.role)) {
    throw new TeamError("Not a member of this team", 403, "forbidden");
  }
  if (!target || !isTeamRole(target.role)) {
    throw new TeamError("Member not found", 404, "member_not_found");
  }
  if (!canManageMemberLimits(actor.role, target.role)) {
    throw new TeamError(
      "You don't have permission to set limits for this member",
      403,
      "forbidden",
    );
  }
  return db.teamMember.update({
    where: { team_id_user_id: { team_id: teamId, user_id: targetUserId } },
    data,
  });
}

export async function removeTeamMember(
  {
    teamId,
    actorUserId,
    targetUserId,
  }: {
    teamId: string;
    actorUserId: string;
    targetUserId: string;
  },
  db: PrismaClient = getPrismaClient(),
) {
  // Under the team lock: an owner cannot leave in the same instant ownership
  // is being transferred to them, which would leave a team without its owner.
  await db.$transaction(async (tx) => {
    await tx.$queryRaw`SELECT id FROM teams WHERE id = ${teamId} FOR UPDATE`;
    const [actor, target] = await Promise.all([
      tx.teamMember.findUnique({
        where: { team_id_user_id: { team_id: teamId, user_id: actorUserId } },
      }),
      tx.teamMember.findUnique({
        where: { team_id_user_id: { team_id: teamId, user_id: targetUserId } },
      }),
    ]);
    if (!actor || !isTeamRole(actor.role)) {
      throw new TeamError("Not a member of this team", 403, "forbidden");
    }
    if (!target || !isTeamRole(target.role)) {
      throw new TeamError("Member not found", 404, "member_not_found");
    }
    if (!canRemoveMember(actorUserId, actor.role, targetUserId, target.role)) {
      throw new TeamError(
        actorUserId === targetUserId
          ? "Transfer team ownership or delete the team before leaving"
          : "You don't have permission to remove this member",
        403,
        "forbidden",
      );
    }
    await tx.teamMember.delete({
      where: { team_id_user_id: { team_id: teamId, user_id: targetUserId } },
    });
  });
}

export async function transferTeamOwnership(
  {
    teamId,
    actorUserId,
    newOwnerUserId,
  }: {
    teamId: string;
    actorUserId: string;
    newOwnerUserId: string;
  },
  db: PrismaClient = getPrismaClient(),
) {
  if (actorUserId === newOwnerUserId) {
    throw new TeamError("You are already the team owner", 400, "already_owner");
  }
  return db.$transaction(async (tx) => {
    await tx.$queryRaw`SELECT id FROM teams WHERE id = ${teamId} FOR UPDATE`;
    const actor = await tx.teamMember.findUnique({
      where: { team_id_user_id: { team_id: teamId, user_id: actorUserId } },
    });
    if (!actor || actor.role !== "owner") {
      throw new TeamError(
        "Only the team owner can transfer ownership",
        403,
        "forbidden",
      );
    }
    const nextOwner = await tx.teamMember.findUnique({
      where: { team_id_user_id: { team_id: teamId, user_id: newOwnerUserId } },
    });
    if (!nextOwner) {
      throw new TeamError(
        "The new owner must already be a team member",
        404,
        "member_not_found",
      );
    }
    await tx.team.update({
      where: { id: teamId },
      data: { owner_id: newOwnerUserId },
    });
    await tx.teamMember.update({
      where: { team_id_user_id: { team_id: teamId, user_id: newOwnerUserId } },
      data: {
        role: "owner",
        monthly_minute_limit: null,
        monthly_ai_edit_limit: null,
      },
    });
    await tx.teamMember.update({
      where: { team_id_user_id: { team_id: teamId, user_id: actorUserId } },
      data: { role: "admin" },
    });
  });
}
