import { createHmac, randomInt, timingSafeEqual } from "node:crypto";

import type { SqlExecutor } from "@/server/user-acquisition";

/**
 * Self-serve email change, in two proofs:
 *
 * 1. a code sent to the address on file (this also verifies an address that
 *    was never verified), then
 * 2. a code sent to the new address.
 *
 * Only then does `finish` move the billing customer(s) to the new address and,
 * in one SQL statement, switch the email, sign out every other session and
 * drop pending password reset links. The old address is told afterwards.
 *
 * Codes are 6 digits, stored only as an HMAC bound to the user, the step and
 * the address, live 10 minutes, are consumed on use and die after 5 wrong
 * guesses. Sends, guesses and passwords are limited per user and per IP, and
 * codes to a new address also per address. Each change is kept in
 * `email_change_log`.
 */

export const EMAIL_CHANGE_LIMITS = {
  codeTtlMs: 10 * 60_000,
  requestTtlMs: 30 * 60_000,
  resendCooldownMs: 60_000,
  maxCodeAttempts: 5,
  /** Accounts without a password re-authenticate by signing in again. */
  sessionFreshMs: 24 * 60 * 60_000,
  /** One finish at a time; a crashed one frees the request after this. */
  finishLeaseMs: 5 * 60_000,
  /** perTarget: codes to one new address, whichever accounts ask, so we can't be used to mail-bomb it. */
  send: { perUser: 5, perIp: 15, perTarget: 3, windowMs: 60 * 60_000 },
  verify: { perUser: 20, perIp: 60, perTarget: null, windowMs: 60 * 60_000 },
  password: { perUser: 5, perIp: 20, perTarget: null, windowMs: 60 * 60_000 },
} as const;

type LimitedAction = "send" | "verify" | "password";

export type EmailChangeStage = "verify_current" | "enter_new" | "verify_new" | "finishing";

export type EmailChangeErrorCode =
  | "password_required"
  | "invalid_password"
  | "reauth_required"
  | "rate_limited"
  | "cooldown"
  | "no_request"
  | "wrong_code"
  | "code_expired"
  | "too_many_attempts"
  | "invalid_email"
  | "same_email"
  | "email_taken"
  | "send_failed"
  | "billing_update_failed"
  | "in_progress";

const STATUS: Record<EmailChangeErrorCode, number> = {
  password_required: 400,
  invalid_password: 400,
  reauth_required: 403,
  rate_limited: 429,
  cooldown: 429,
  no_request: 409,
  wrong_code: 400,
  code_expired: 400,
  too_many_attempts: 400,
  invalid_email: 400,
  same_email: 400,
  email_taken: 409,
  send_failed: 502,
  billing_update_failed: 502,
  in_progress: 409,
};

export class EmailChangeError extends Error {
  readonly status: number;

  constructor(
    readonly code: EmailChangeErrorCode,
    readonly details: { retryAfterSeconds?: number; attemptsLeft?: number } = {},
  ) {
    super(code);
    this.name = "EmailChangeError";
    this.status = STATUS[code];
  }
}

export type EmailChangeMessage =
  | { kind: "current_code"; code: string }
  | { kind: "new_code"; code: string; new_email: string }
  | { kind: "changed"; old_email: string; new_email: string };

export interface EmailChangeDeps {
  sql: SqlExecutor;
  /** HMAC key for codes and IPs (BETTER_AUTH_SECRET). */
  secret: string;
  now(): Date;
  generateCode(): string;
  sendEmail(userId: string, message: EmailChangeMessage): Promise<void>;
  /** Moves billing customers to `to`; returns an undo. Throws, having undone its own partial work, on failure. */
  updateBillingEmail(userId: string, from: string, to: string): Promise<() => Promise<void>>;
  verifyPassword(password: string): Promise<boolean>;
}

export interface EmailChangeActor {
  userId: string;
  sessionId: string;
  sessionCreatedAt: Date;
  ip: string | null;
}

export interface EmailChangeState {
  email: string;
  emailVerified: boolean;
  hasPassword: boolean;
  request: {
    stage: EmailChangeStage;
    newEmail: string | null;
    codeExpiresAt: string | null;
    resendAvailableAt: string | null;
    expiresAt: string;
  } | null;
}

interface UserRow {
  email: string;
  emailVerified: boolean;
  has_password: boolean;
}

interface RequestRow {
  session_id: string;
  stage: EmailChangeStage;
  current_email: string;
  new_email: string | null;
  code_hash: string | null;
  code_sent_at: Date | null;
  code_expires_at: Date | null;
  code_attempts: number;
  expires_at: Date;
}

export function generateEmailChangeCode(): string {
  return String(randomInt(0, 1_000_000)).padStart(6, "0");
}

// Better Auth's sign-in and password reset check addresses with zod's `z.email()`
// (this is its pattern), so anything looser could leave the account unable to sign in.
// Better Auth stores addresses lowercased.
const EMAIL_PATTERN =
  /^(?:[A-Za-z0-9_'+\-]+\.)*[A-Za-z0-9_'+\-]*[A-Za-z0-9_+-]@(?:[A-Za-z0-9][A-Za-z0-9\-]*\.)+[A-Za-z]{2,}$/;

export function normalizeEmail(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const email = value.trim().toLowerCase();
  return email.length <= 254 && EMAIL_PATTERN.test(email) ? email : null;
}

function hmac(secret: string, value: string) {
  return createHmac("sha256", secret).update(value).digest("hex");
}

function codeHash(deps: EmailChangeDeps, userId: string, stage: EmailChangeStage, email: string, code: string) {
  return hmac(deps.secret, `email-change:${userId}:${stage}:${email}:${code}`);
}

function ipHash(deps: EmailChangeDeps, ip: string | null) {
  return ip ? hmac(deps.secret, `email-change-ip:${ip}`) : null;
}

function targetHash(deps: EmailChangeDeps, email: string | null) {
  return email ? hmac(deps.secret, `email-change-target:${email}`) : null;
}

function sameHash(a: string, b: string) {
  const left = Buffer.from(a);
  const right = Buffer.from(b);
  return left.length === right.length && timingSafeEqual(left, right);
}

function later(now: Date, ms: number) {
  return new Date(now.getTime() + ms);
}

function secondsUntil(now: Date, at: Date) {
  return Math.max(1, Math.ceil((at.getTime() - now.getTime()) / 1000));
}

function isUniqueViolation(error: unknown) {
  const err = error as { code?: string; meta?: { code?: string }; message?: string } | null;
  return err?.code === "23505" || err?.meta?.code === "23505" || /23505|unique constraint/i.test(err?.message ?? "");
}

async function loadUser(deps: EmailChangeDeps, userId: string): Promise<UserRow> {
  const [user] = await deps.sql<UserRow>(
    `SELECT u.email, u."emailVerified",
            EXISTS (SELECT 1 FROM account a WHERE a."userId" = u.id AND a."providerId" = 'credential' AND a.password IS NOT NULL) AS has_password
       FROM users u WHERE u.id = $1`,
    [userId],
  );
  if (!user) throw new EmailChangeError("no_request");
  return user;
}

/** The actor's open request, or null when there is none, it expired, another session owns it or the email moved on. */
async function loadRequest(deps: EmailChangeDeps, actor: EmailChangeActor, currentEmail: string): Promise<RequestRow | null> {
  const [request] = await deps.sql<RequestRow>(
    `SELECT session_id, stage, current_email, new_email, code_hash, code_sent_at, code_expires_at, code_attempts, expires_at
       FROM email_change_requests WHERE user_id = $1`,
    [actor.userId],
  );
  if (!request) return null;
  if (request.expires_at <= deps.now() || request.current_email !== currentEmail) {
    await deps.sql(
      `DELETE FROM email_change_requests WHERE user_id = $1 AND (expires_at <= $2 OR current_email <> $3) RETURNING user_id`,
      [actor.userId, deps.now(), currentEmail],
    );
    return null;
  }
  return request.session_id === actor.sessionId ? request : null;
}

async function requireRequest(deps: EmailChangeDeps, actor: EmailChangeActor, stages: EmailChangeStage[]) {
  const user = await loadUser(deps, actor.userId);
  const request = await loadRequest(deps, actor, user.email);
  if (!request || !stages.includes(request.stage)) throw new EmailChangeError("no_request");
  return { user, request };
}

/**
 * Records the attempt, then counts it with every other one in the window, so
 * parallel requests can't all pass a check made before any of them counted.
 * Over the limit, the attempt is taken back and refused. `target` is a new
 * address that a code would go to.
 */
async function takeSlot(
  deps: EmailChangeDeps,
  actor: EmailChangeActor,
  action: LimitedAction,
  target: string | null = null,
): Promise<string> {
  const limit = EMAIL_CHANGE_LIMITS[action];
  const now = deps.now();
  const ip = ipHash(deps, actor.ip);
  const to = limit.perTarget ? targetHash(deps, target) : null;
  const [slot] = await deps.sql<{ id: string }>(
    `INSERT INTO email_change_events (user_id, ip_hash, target_hash, action, created_at) VALUES ($1, $2, $3, $4, $5) RETURNING id::text`,
    [actor.userId, ip, to, action, now],
  );
  const [row] = await deps.sql<{
    by_user: number;
    by_ip: number;
    by_target: number;
    oldest_user: Date | null;
    oldest_ip: Date | null;
    oldest_target: Date | null;
  }>(
    `SELECT count(*) FILTER (WHERE user_id = $1)::int AS by_user,
            count(*) FILTER (WHERE ip_hash = $2)::int AS by_ip,
            count(*) FILTER (WHERE target_hash = $3)::int AS by_target,
            min(created_at) FILTER (WHERE user_id = $1) AS oldest_user,
            min(created_at) FILTER (WHERE ip_hash = $2) AS oldest_ip,
            min(created_at) FILTER (WHERE target_hash = $3) AS oldest_target
       FROM email_change_events
      WHERE action = $4 AND created_at > $5 AND (user_id = $1 OR ip_hash = $2 OR target_hash = $3)`,
    [actor.userId, ip, to, action, new Date(now.getTime() - limit.windowMs)],
  );
  const oldest =
    row.by_user > limit.perUser
      ? row.oldest_user
      : ip && row.by_ip > limit.perIp
        ? row.oldest_ip
        : to && limit.perTarget && row.by_target > limit.perTarget
          ? row.oldest_target
          : null;
  if (oldest) {
    await releaseSlot(deps, slot.id);
    throw new EmailChangeError("rate_limited", {
      retryAfterSeconds: secondsUntil(now, later(new Date(oldest), limit.windowMs)),
    });
  }
  return slot.id;
}

async function releaseSlot(deps: EmailChangeDeps, slotId: string) {
  await deps.sql(`DELETE FROM email_change_events WHERE id = $1::bigint RETURNING id`, [slotId]);
}

async function sendOrRollBack(deps: EmailChangeDeps, actor: EmailChangeActor, message: EmailChangeMessage) {
  try {
    await deps.sendEmail(actor.userId, message);
  } catch (error) {
    console.error("Email change code delivery failed", error);
    // No usable code is out there; let them ask again straight away.
    await deps.sql(
      `UPDATE email_change_requests SET code_hash = NULL, code_sent_at = NULL, code_expires_at = NULL
        WHERE user_id = $1 AND session_id = $2 RETURNING user_id`,
      [actor.userId, actor.sessionId],
    );
    throw new EmailChangeError("send_failed");
  }
}

export async function getEmailChangeState(deps: EmailChangeDeps, actor: EmailChangeActor): Promise<EmailChangeState> {
  const user = await loadUser(deps, actor.userId);
  const request = await loadRequest(deps, actor, user.email);
  return {
    email: user.email,
    emailVerified: user.emailVerified,
    hasPassword: user.has_password,
    request: request && {
      stage: request.stage,
      newEmail: request.new_email,
      codeExpiresAt: request.code_hash && request.code_expires_at ? request.code_expires_at.toISOString() : null,
      resendAvailableAt: request.code_sent_at
        ? later(request.code_sent_at, EMAIL_CHANGE_LIMITS.resendCooldownMs).toISOString()
        : null,
      expiresAt: request.expires_at.toISOString(),
    },
  };
}

/** Re-authenticates, then sends a code to the current address. Replaces any open request. */
export async function startEmailChange(
  deps: EmailChangeDeps,
  actor: EmailChangeActor,
  { password }: { password?: string },
): Promise<EmailChangeState> {
  const user = await loadUser(deps, actor.userId);
  const now = deps.now();

  if (user.has_password) {
    if (!password) throw new EmailChangeError("password_required");
    // Every check holds a slot while it runs; only wrong passwords keep it.
    const slot = await takeSlot(deps, actor, "password");
    if (!(await deps.verifyPassword(password))) throw new EmailChangeError("invalid_password");
    await releaseSlot(deps, slot);
  } else if (now.getTime() - actor.sessionCreatedAt.getTime() > EMAIL_CHANGE_LIMITS.sessionFreshMs) {
    throw new EmailChangeError("reauth_required");
  }

  const slot = await takeSlot(deps, actor, "send");
  const code = deps.generateCode();
  const rows = await deps.sql<{ user_id: string }>(
    `INSERT INTO email_change_requests AS r
            (user_id, session_id, stage, current_email, new_email, code_hash, code_sent_at, code_expires_at, code_attempts, expires_at, created_at)
     VALUES ($1, $2, 'verify_current', $3, NULL, $4, $5, $6, 0, $7, $5)
     ON CONFLICT (user_id) DO UPDATE SET
       session_id = EXCLUDED.session_id, stage = EXCLUDED.stage, current_email = EXCLUDED.current_email,
       new_email = NULL, code_hash = EXCLUDED.code_hash, code_sent_at = EXCLUDED.code_sent_at,
       code_expires_at = EXCLUDED.code_expires_at, code_attempts = 0, expires_at = EXCLUDED.expires_at,
       created_at = EXCLUDED.created_at, finish_claimed_at = NULL
     WHERE r.code_sent_at IS NULL OR r.code_sent_at <= $8 OR r.expires_at <= $5
     RETURNING user_id`,
    [
      actor.userId,
      actor.sessionId,
      user.email,
      codeHash(deps, actor.userId, "verify_current", user.email, code),
      now,
      later(now, EMAIL_CHANGE_LIMITS.codeTtlMs),
      later(now, EMAIL_CHANGE_LIMITS.requestTtlMs),
      new Date(now.getTime() - EMAIL_CHANGE_LIMITS.resendCooldownMs),
    ],
  );
  if (rows.length === 0) await throwCooldown(deps, actor.userId, slot);
  await deps.sql(`DELETE FROM email_change_events WHERE created_at < $1 RETURNING id`, [
    new Date(now.getTime() - 24 * 60 * 60_000),
  ]);
  await sendOrRollBack(deps, actor, { kind: "current_code", code });
  return getEmailChangeState(deps, actor);
}

async function throwCooldown(deps: EmailChangeDeps, userId: string, slot: string): Promise<never> {
  await releaseSlot(deps, slot);
  const [row] = await deps.sql<{ code_sent_at: Date | null }>(
    `SELECT code_sent_at FROM email_change_requests WHERE user_id = $1`,
    [userId],
  );
  const at = row?.code_sent_at ? later(row.code_sent_at, EMAIL_CHANGE_LIMITS.resendCooldownMs) : later(deps.now(), 1000);
  throw new EmailChangeError("cooldown", { retryAfterSeconds: secondsUntil(deps.now(), at) });
}

/** Stores a fresh code for the request's current step and sends it. */
async function issueCode(
  deps: EmailChangeDeps,
  actor: EmailChangeActor,
  request: RequestRow,
  stage: "verify_current" | "verify_new",
  newEmail: string | null,
) {
  const target = stage === "verify_current" ? request.current_email : newEmail!;
  const slot = await takeSlot(deps, actor, "send", stage === "verify_new" ? target : null);
  // Behind the send limit (the attempt keeps its slot), so this can't be used to look up addresses in bulk.
  if (stage === "verify_new" && (await emailTaken(deps, actor.userId, target))) throw new EmailChangeError("email_taken");
  const now = deps.now();
  const code = deps.generateCode();
  // A different new address may be sent at once; the same one waits out the cooldown.
  const rows = await deps.sql<{ user_id: string }>(
    `UPDATE email_change_requests
        SET stage = $3, new_email = $4, code_hash = $5, code_sent_at = $6, code_expires_at = $7, code_attempts = 0,
            expires_at = GREATEST(expires_at, $7)
      WHERE user_id = $1 AND session_id = $2 AND stage = $8
        AND (code_sent_at IS NULL OR code_sent_at <= $9 OR new_email IS DISTINCT FROM $4)
      RETURNING user_id`,
    [
      actor.userId,
      actor.sessionId,
      stage,
      newEmail,
      codeHash(deps, actor.userId, stage, target, code),
      now,
      later(now, EMAIL_CHANGE_LIMITS.codeTtlMs),
      request.stage,
      new Date(now.getTime() - EMAIL_CHANGE_LIMITS.resendCooldownMs),
    ],
  );
  if (rows.length === 0) await throwCooldown(deps, actor.userId, slot);
  await sendOrRollBack(
    deps,
    actor,
    stage === "verify_current" ? { kind: "current_code", code } : { kind: "new_code", code, new_email: target },
  );
}

export async function resendEmailChangeCode(deps: EmailChangeDeps, actor: EmailChangeActor): Promise<EmailChangeState> {
  const { request } = await requireRequest(deps, actor, ["verify_current", "verify_new"]);
  const stage = request.stage as "verify_current" | "verify_new";
  await issueCode(deps, actor, request, stage, request.new_email);
  return getEmailChangeState(deps, actor);
}

/** Checks a code for the current step. Every guess counts, also concurrent ones. */
async function consumeCode(deps: EmailChangeDeps, actor: EmailChangeActor, stage: "verify_current" | "verify_new", rawCode: unknown) {
  const { request } = await requireRequest(deps, actor, [stage]);
  await takeSlot(deps, actor, "verify");
  const now = deps.now();
  const max = EMAIL_CHANGE_LIMITS.maxCodeAttempts;

  const [counted] = await deps.sql<{ code_hash: string; code_attempts: number }>(
    `UPDATE email_change_requests SET code_attempts = code_attempts + 1
      WHERE user_id = $1 AND session_id = $2 AND stage = $3 AND code_hash IS NOT NULL
        AND code_expires_at > $4 AND code_attempts < $5
      RETURNING code_hash, code_attempts`,
    [actor.userId, actor.sessionId, stage, now, max],
  );
  if (!counted) {
    if (!request.code_hash) throw new EmailChangeError("code_expired");
    if (request.code_attempts >= max) throw new EmailChangeError("too_many_attempts");
    throw new EmailChangeError("code_expired");
  }

  const code = typeof rawCode === "string" ? rawCode.replace(/\s+/g, "") : "";
  const target = stage === "verify_current" ? request.current_email : request.new_email!;
  const hash = codeHash(deps, actor.userId, stage, target, code);
  const attemptsLeft = max - counted.code_attempts;
  if (!/^\d{6}$/.test(code) || !sameHash(hash, counted.code_hash)) {
    if (attemptsLeft <= 0) throw new EmailChangeError("too_many_attempts");
    throw new EmailChangeError("wrong_code", { attemptsLeft });
  }
  return { request, hash };
}

/** Step 1: the code from the current address. Proves (and, if needed, verifies) it. */
export async function verifyCurrentEmailCode(
  deps: EmailChangeDeps,
  actor: EmailChangeActor,
  code: unknown,
): Promise<EmailChangeState> {
  const { request, hash } = await consumeCode(deps, actor, "verify_current", code);
  const [moved] = await deps.sql<{ user_id: string }>(
    `WITH moved AS (
       UPDATE email_change_requests
          SET stage = 'enter_new', code_hash = NULL, code_sent_at = NULL, code_expires_at = NULL, code_attempts = 0
        WHERE user_id = $1 AND session_id = $2 AND stage = 'verify_current' AND code_hash = $3
        RETURNING user_id
     ), verified AS (
       UPDATE users SET "emailVerified" = true, "updatedAt" = $5
        WHERE id IN (SELECT user_id FROM moved) AND email = $4 AND "emailVerified" = false
        RETURNING id
     )
     SELECT user_id FROM moved`,
    [actor.userId, actor.sessionId, hash, request.current_email, deps.now()],
  );
  // Another request with the same code got there first.
  if (!moved) throw new EmailChangeError("wrong_code", { attemptsLeft: 0 });
  return getEmailChangeState(deps, actor);
}

/** Step 2a: the new address. Rejected when another account uses it, which sign-up reveals too (see `issueCode`). */
export async function submitNewEmail(
  deps: EmailChangeDeps,
  actor: EmailChangeActor,
  rawEmail: unknown,
): Promise<EmailChangeState> {
  const { request } = await requireRequest(deps, actor, ["enter_new", "verify_new"]);
  const newEmail = normalizeEmail(rawEmail);
  if (!newEmail) throw new EmailChangeError("invalid_email");
  if (newEmail === request.current_email.toLowerCase()) throw new EmailChangeError("same_email");
  await issueCode(deps, actor, request, "verify_new", newEmail);
  return getEmailChangeState(deps, actor);
}

async function emailTaken(deps: EmailChangeDeps, userId: string, email: string) {
  const rows = await deps.sql<{ id: string }>(`SELECT id FROM users WHERE lower(email) = $1 AND id <> $2 LIMIT 1`, [
    email,
    userId,
  ]);
  return rows.length > 0;
}

export interface EmailChangeResult {
  email: string;
  sessionsRevoked: number;
}

/** Step 2b: the code from the new address, then the switch. */
export async function verifyNewEmailCode(
  deps: EmailChangeDeps,
  actor: EmailChangeActor,
  code: unknown,
): Promise<EmailChangeResult> {
  const { hash } = await consumeCode(deps, actor, "verify_new", code);
  const [moved] = await deps.sql<{ user_id: string }>(
    `UPDATE email_change_requests
        SET stage = 'finishing', code_hash = NULL, code_sent_at = NULL, code_expires_at = NULL, code_attempts = 0
      WHERE user_id = $1 AND session_id = $2 AND stage = 'verify_new' AND code_hash = $3
      RETURNING user_id`,
    [actor.userId, actor.sessionId, hash],
  );
  if (!moved) throw new EmailChangeError("wrong_code", { attemptsLeft: 0 });
  return finishEmailChange(deps, actor);
}

/**
 * Billing first, then the account, so receipts never keep going to the old
 * address unnoticed. A billing failure leaves the email unchanged and the
 * request in `finishing`, which this retries without new codes. One finish
 * runs at a time: a second one could otherwise undo the billing change of the
 * one that switched the email.
 */
export async function finishEmailChange(deps: EmailChangeDeps, actor: EmailChangeActor): Promise<EmailChangeResult> {
  const { request } = await requireRequest(deps, actor, ["finishing"]);
  const oldEmail = request.current_email;
  const newEmail = request.new_email!;
  const now = deps.now();
  const [claimed] = await deps.sql<{ user_id: string }>(
    `UPDATE email_change_requests SET finish_claimed_at = $3
      WHERE user_id = $1 AND session_id = $2 AND stage = 'finishing' AND (finish_claimed_at IS NULL OR finish_claimed_at <= $4)
      RETURNING user_id`,
    [actor.userId, actor.sessionId, now, new Date(now.getTime() - EMAIL_CHANGE_LIMITS.finishLeaseMs)],
  );
  if (!claimed) throw new EmailChangeError("in_progress");

  let result: Awaited<ReturnType<typeof switchEmail>>;
  try {
    result = await switchEmail(deps, actor, oldEmail, newEmail);
  } catch (error) {
    await deps.sql(
      `UPDATE email_change_requests SET finish_claimed_at = NULL WHERE user_id = $1 AND session_id = $2 AND finish_claimed_at = $3 RETURNING user_id`,
      [actor.userId, actor.sessionId, now],
    );
    throw error;
  }

  try {
    await deps.sendEmail(actor.userId, { kind: "changed", old_email: oldEmail, new_email: newEmail });
  } catch (error) {
    // The change itself succeeded; a missing notice must not undo it.
    console.error("Email change: notice to the previous address failed", error);
  }
  return { email: newEmail, sessionsRevoked: result.sessions_revoked };
}

async function switchEmail(deps: EmailChangeDeps, actor: EmailChangeActor, oldEmail: string, newEmail: string) {
  if (await emailTaken(deps, actor.userId, newEmail)) {
    await backToNewEmail(deps, actor);
    throw new EmailChangeError("email_taken");
  }

  let undoBilling: () => Promise<void>;
  try {
    undoBilling = await deps.updateBillingEmail(actor.userId, oldEmail, newEmail);
  } catch (error) {
    console.error("Email change: billing email update failed", error);
    throw new EmailChangeError("billing_update_failed");
  }

  let result: { updated: number; sessions_revoked: number } | undefined;
  try {
    [result] = await deps.sql<{ updated: number; sessions_revoked: number }>(
      `WITH req AS (
         DELETE FROM email_change_requests
          WHERE user_id = $1 AND session_id = $2 AND stage = 'finishing' AND current_email = $3 AND new_email = $4
          RETURNING user_id
       ), updated AS (
         UPDATE users SET email = $4, "emailVerified" = true, "updatedAt" = $5
          WHERE id IN (SELECT user_id FROM req) AND email = $3
          RETURNING id
       ), sessions AS (
         DELETE FROM session WHERE "userId" IN (SELECT id FROM updated) AND id <> $2 RETURNING id
       ), resets AS (
         DELETE FROM verification WHERE value IN (SELECT id FROM updated) AND identifier LIKE 'reset-password:%' RETURNING id
       ), logged AS (
         INSERT INTO email_change_log (user_id, old_email, new_email, session_id, ip_hash, changed_at)
         SELECT id, $3, $4, $2, $6, $5 FROM updated
         RETURNING id
       )
       SELECT (SELECT count(*) FROM updated)::int AS updated, (SELECT count(*) FROM sessions)::int AS sessions_revoked,
              (SELECT count(*) FROM logged)::int AS logged`,
      [actor.userId, actor.sessionId, oldEmail, newEmail, deps.now(), ipHash(deps, actor.ip)],
    );
  } catch (error) {
    await undoBilling().catch((undoError) => console.error("Email change: billing email undo failed", undoError));
    if (isUniqueViolation(error)) {
      await backToNewEmail(deps, actor);
      throw new EmailChangeError("email_taken");
    }
    throw error;
  }

  if (!result || result.updated !== 1) {
    await undoBilling().catch((undoError) => console.error("Email change: billing email undo failed", undoError));
    throw new EmailChangeError("no_request");
  }
  return result;
}

async function backToNewEmail(deps: EmailChangeDeps, actor: EmailChangeActor) {
  await deps.sql(
    `UPDATE email_change_requests SET stage = 'enter_new', new_email = NULL, finish_claimed_at = NULL
      WHERE user_id = $1 AND session_id = $2 RETURNING user_id`,
    [actor.userId, actor.sessionId],
  );
}

export async function cancelEmailChange(deps: EmailChangeDeps, actor: EmailChangeActor): Promise<void> {
  await deps.sql(`DELETE FROM email_change_requests WHERE user_id = $1 RETURNING user_id`, [actor.userId]);
}
