// @vitest-environment node
import { readFileSync } from "node:fs";

import { PGlite } from "@electric-sql/pglite";
import { uuid_ossp } from "@electric-sql/pglite/contrib/uuid_ossp";

import {
  EMAIL_CHANGE_LIMITS,
  EmailChangeError,
  finishEmailChange,
  getEmailChangeState,
  resendEmailChangeCode,
  startEmailChange,
  submitNewEmail,
  verifyCurrentEmailCode,
  verifyNewEmailCode,
  type EmailChangeActor,
  type EmailChangeDeps,
  type EmailChangeMessage,
} from "./email-change";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");

const OLD = "owner@example.com";
const NEW = "fresh@example.com";

describe("email change (real Postgres)", () => {
  let db: PGlite;
  let clock: Date;
  let codes: string[];
  let sent: EmailChangeMessage[];
  let deps: EmailChangeDeps;
  let billing: { calls: [string, string][]; undone: number; fail: boolean };
  let actor: EmailChangeActor;

  beforeAll(async () => {
    db = new PGlite({ extensions: { uuid_ossp } });
    await db.exec(read("../../../init.sql"));
    // Production databases get the tables from the Prisma migration, so test that DDL.
    await db.exec("DROP TABLE email_change_log; DROP TABLE email_change_events; DROP TABLE email_change_requests;");
    await db.exec(read("../../prisma/migrations/20261119120000_email_change_requests/migration.sql"));
  });

  afterAll(async () => {
    await db.close();
  });

  beforeEach(async () => {
    await db.exec(`
      DELETE FROM email_change_log; DELETE FROM email_change_events; DELETE FROM email_change_requests;
      DELETE FROM verification; DELETE FROM session; DELETE FROM account; DELETE FROM users;
      INSERT INTO users (id, name, email, "emailVerified") VALUES
        ('u1', 'Owner', '${OLD}', true), ('u2', 'Other', 'taken@example.com', true);
      INSERT INTO account (id, "accountId", "providerId", "userId", password, "createdAt", "updatedAt")
        VALUES ('a1', 'u1', 'credential', 'u1', 'hashed', now(), now());
      INSERT INTO session (id, "expiresAt", token, "createdAt", "updatedAt", "userId") VALUES
        ('s1', now() + interval '7 days', 't1', now(), now(), 'u1'),
        ('s2', now() + interval '7 days', 't2', now(), now(), 'u1'),
        ('s3', now() + interval '7 days', 't3', now(), now(), 'u2');
      INSERT INTO verification (id, identifier, value, "expiresAt") VALUES
        ('v1', 'reset-password:old-link', 'u1', now() + interval '1 hour'),
        ('v2', 'reset-password:other', 'u2', now() + interval '1 hour');
    `);
    clock = new Date("2026-11-19T10:00:00Z");
    codes = ["111111", "222222", "333333", "444444", "555555", "666666", "777777", "888888"];
    sent = [];
    billing = { calls: [], undone: 0, fail: false };
    actor = { userId: "u1", sessionId: "s1", sessionCreatedAt: clock, ip: "203.0.113.7" };
    deps = {
      sql: async <T,>(query: string, params: unknown[]) => (await db.query<T>(query, params)).rows,
      secret: "test-secret",
      now: () => clock,
      generateCode: () => codes.shift()!,
      sendEmail: async (_userId, message) => {
        sent.push(message);
      },
      updateBillingEmail: async (_userId, from, to) => {
        if (billing.fail) throw new Error("stripe down");
        billing.calls.push([from, to]);
        return async () => {
          billing.undone += 1;
        };
      },
      verifyPassword: async (password) => password === "correct horse",
    };
  });

  const advance = (ms: number) => {
    clock = new Date(clock.getTime() + ms);
  };
  const user = async (id = "u1") =>
    (await db.query<{ email: string; emailVerified: boolean }>(`SELECT email, "emailVerified" FROM users WHERE id = $1`, [id])).rows[0];
  const expectError = async (promise: Promise<unknown>, code: string) => {
    const error = await promise.then(() => null, (e: unknown) => e);
    expect(error).toBeInstanceOf(EmailChangeError);
    expect((error as EmailChangeError).code).toBe(code);
    return error as EmailChangeError;
  };
  const toNewEmailStep = async () => {
    await startEmailChange(deps, actor, { password: "correct horse" });
    await verifyCurrentEmailCode(deps, actor, "111111");
  };

  it("changes the email after both codes, signs out other sessions and tells the old address", async () => {
    const started = await startEmailChange(deps, actor, { password: "correct horse" });
    expect(started.request?.stage).toBe("verify_current");
    expect(sent).toEqual([{ kind: "current_code", code: "111111" }]);

    expect((await verifyCurrentEmailCode(deps, actor, " 111 111 ")).request?.stage).toBe("enter_new");
    const waiting = await submitNewEmail(deps, actor, "  Fresh@Example.com ");
    expect(waiting.request).toMatchObject({ stage: "verify_new", newEmail: NEW });
    expect(sent[1]).toEqual({ kind: "new_code", code: "222222", new_email: NEW });

    const done = await verifyNewEmailCode(deps, actor, "222222");
    expect(done).toEqual({ email: NEW, sessionsRevoked: 1 });
    expect(await user()).toEqual({ email: NEW, emailVerified: true });
    expect(billing.calls).toEqual([[OLD, NEW]]);
    expect(sent[2]).toEqual({ kind: "changed", old_email: OLD, new_email: NEW });

    const sessions = (await db.query<{ id: string }>("SELECT id FROM session ORDER BY id")).rows.map((r) => r.id);
    expect(sessions).toEqual(["s1", "s3"]);
    const resets = (await db.query<{ id: string }>("SELECT id FROM verification")).rows.map((r) => r.id);
    expect(resets).toEqual(["v2"]);
    expect((await getEmailChangeState(deps, actor)).request).toBeNull();

    const log = (await db.query<{ user_id: string; old_email: string; new_email: string; session_id: string; ip_hash: string; notified_at: Date | null }>(
      "SELECT user_id, old_email, new_email, session_id, ip_hash, notified_at FROM email_change_log",
    )).rows;
    expect(log).toEqual([
      { user_id: "u1", old_email: OLD, new_email: NEW, session_id: "s1", ip_hash: expect.stringMatching(/^[0-9a-f]{64}$/), notified_at: null },
    ]);
  });

  it("stores codes only as an HMAC", async () => {
    await startEmailChange(deps, actor, { password: "correct horse" });
    const row = (await db.query<{ code_hash: string }>("SELECT code_hash FROM email_change_requests")).rows[0];
    expect(row.code_hash).toMatch(/^[0-9a-f]{64}$/);
    expect(row.code_hash).not.toContain("111111");
    const ips = (await db.query<{ ip_hash: string }>("SELECT ip_hash FROM email_change_events")).rows;
    expect(ips[0].ip_hash).toMatch(/^[0-9a-f]{64}$/);
  });

  it("requires the password, and limits wrong ones", async () => {
    await expectError(startEmailChange(deps, actor, {}), "password_required");
    for (let i = 0; i < EMAIL_CHANGE_LIMITS.password.perUser; i += 1) {
      await expectError(startEmailChange(deps, actor, { password: "nope" }), "invalid_password");
    }
    const limited = await expectError(startEmailChange(deps, actor, { password: "correct horse" }), "rate_limited");
    expect(limited.details.retryAfterSeconds).toBeGreaterThan(0);
    expect(sent).toEqual([]);
  });

  it("asks password-less accounts to sign in again when the session is old", async () => {
    await db.exec("DELETE FROM account");
    const stale = { ...actor, sessionCreatedAt: new Date(clock.getTime() - EMAIL_CHANGE_LIMITS.sessionFreshMs - 1) };
    await expectError(startEmailChange(deps, stale, {}), "reauth_required");
    expect((await startEmailChange(deps, actor, {})).request?.stage).toBe("verify_current");
  });

  it("verifies a never-verified current address as part of step one", async () => {
    await db.exec(`UPDATE users SET "emailVerified" = false WHERE id = 'u1'`);
    const started = await startEmailChange(deps, actor, { password: "correct horse" });
    expect(started.emailVerified).toBe(false);
    expect(sent[0]).toEqual({ kind: "current_code", code: "111111" });
    const verified = await verifyCurrentEmailCode(deps, actor, "111111");
    expect(verified.emailVerified).toBe(true);
    expect(await user()).toEqual({ email: OLD, emailVerified: true });
  });

  it("rejects wrong codes, then locks the code after five guesses", async () => {
    await startEmailChange(deps, actor, { password: "correct horse" });
    const first = await expectError(verifyCurrentEmailCode(deps, actor, "999999"), "wrong_code");
    expect(first.details.attemptsLeft).toBe(4);
    for (let i = 0; i < 3; i += 1) await expectError(verifyCurrentEmailCode(deps, actor, "999999"), "wrong_code");
    await expectError(verifyCurrentEmailCode(deps, actor, "999999"), "too_many_attempts");
    // Even the right code is dead now.
    await expectError(verifyCurrentEmailCode(deps, actor, "111111"), "too_many_attempts");
    // A new code works.
    advance(EMAIL_CHANGE_LIMITS.resendCooldownMs);
    await resendEmailChangeCode(deps, actor);
    expect((await verifyCurrentEmailCode(deps, actor, "222222")).request?.stage).toBe("enter_new");
  });

  it("counts concurrent guesses against the same limit", async () => {
    await startEmailChange(deps, actor, { password: "correct horse" });
    const guesses = await Promise.allSettled(
      Array.from({ length: 10 }, (_, i) => verifyCurrentEmailCode(deps, actor, String(900000 + i))),
    );
    expect(guesses.every((g) => g.status === "rejected")).toBe(true);
    const row = (await db.query<{ code_attempts: number }>("SELECT code_attempts FROM email_change_requests")).rows[0];
    expect(row.code_attempts).toBe(EMAIL_CHANGE_LIMITS.maxCodeAttempts);
  });

  it("rejects an expired code", async () => {
    await startEmailChange(deps, actor, { password: "correct horse" });
    advance(EMAIL_CHANGE_LIMITS.codeTtlMs + 1);
    await expectError(verifyCurrentEmailCode(deps, actor, "111111"), "code_expired");
  });

  it("uses each code once", async () => {
    await toNewEmailStep();
    await expectError(verifyCurrentEmailCode(deps, actor, "111111"), "no_request");
  });

  it("never accepts the current address's code for the new address", async () => {
    await toNewEmailStep();
    await submitNewEmail(deps, actor, NEW);
    await expectError(verifyNewEmailCode(deps, actor, "111111"), "wrong_code");
    expect((await user()).email).toBe(OLD);
  });

  it("ignores requests from another session", async () => {
    await startEmailChange(deps, actor, { password: "correct horse" });
    const other = { ...actor, sessionId: "s2" };
    expect((await getEmailChangeState(deps, other)).request).toBeNull();
    await expectError(verifyCurrentEmailCode(deps, other, "111111"), "no_request");
  });

  it("drops the whole request after 30 minutes", async () => {
    await toNewEmailStep();
    advance(EMAIL_CHANGE_LIMITS.requestTtlMs + 1);
    await expectError(submitNewEmail(deps, actor, NEW), "no_request");
  });

  it("enforces a resend cooldown and an hourly send limit", async () => {
    await startEmailChange(deps, actor, { password: "correct horse" });
    const cooldown = await expectError(resendEmailChangeCode(deps, actor), "cooldown");
    expect(cooldown.details.retryAfterSeconds).toBe(60);
    for (let i = 1; i < EMAIL_CHANGE_LIMITS.send.perUser; i += 1) {
      advance(EMAIL_CHANGE_LIMITS.resendCooldownMs);
      await resendEmailChangeCode(deps, actor);
    }
    advance(EMAIL_CHANGE_LIMITS.resendCooldownMs);
    await expectError(resendEmailChangeCode(deps, actor), "rate_limited");
    expect(sent).toHaveLength(EMAIL_CHANGE_LIMITS.send.perUser);
    // An hour later the request itself has expired; starting over is allowed again.
    advance(EMAIL_CHANGE_LIMITS.send.windowMs);
    expect((await startEmailChange(deps, actor, { password: "correct horse" })).request?.stage).toBe("verify_current");
  });

  it("limits sends per IP across accounts", async () => {
    await startEmailChange(deps, actor, { password: "correct horse" });
    const ipHash = (await db.query<{ ip_hash: string }>("SELECT ip_hash FROM email_change_events LIMIT 1")).rows[0].ip_hash;
    for (let i = 0; i < EMAIL_CHANGE_LIMITS.send.perIp; i += 1) {
      await db.query(`INSERT INTO email_change_events (user_id, ip_hash, action, created_at) VALUES ('u2', $1, 'send', $2)`, [ipHash, clock]);
    }
    advance(EMAIL_CHANGE_LIMITS.resendCooldownMs);
    await expectError(resendEmailChangeCode(deps, actor), "rate_limited");
    // The same account from another network is only held by its own limit.
    await resendEmailChangeCode(deps, { ...actor, ip: "198.51.100.9" });
  });

  it("limits code guesses per user across fresh codes", async () => {
    await startEmailChange(deps, actor, { password: "correct horse" });
    for (let i = 0; i < EMAIL_CHANGE_LIMITS.verify.perUser; i += 1) {
      await db.query(`INSERT INTO email_change_events (user_id, ip_hash, action, created_at) VALUES ('u1', NULL, 'verify', $1)`, [clock]);
    }
    await expectError(verifyCurrentEmailCode(deps, actor, "111111"), "rate_limited");
  });

  it("rejects an address another account uses, in any letter case", async () => {
    await toNewEmailStep();
    await expectError(submitNewEmail(deps, actor, "Taken@Example.com"), "email_taken");
    await expectError(submitNewEmail(deps, actor, OLD.toUpperCase()), "same_email");
    await expectError(submitNewEmail(deps, actor, "not an email"), "invalid_email");
    await expectError(submitNewEmail(deps, actor, "Name <x@example.com>"), "invalid_email");
    expect(sent.filter((m) => m.kind === "new_code")).toEqual([]);
  });

  it("sends the user back to pick another address when one is taken at the last moment", async () => {
    await toNewEmailStep();
    await submitNewEmail(deps, actor, NEW);
    await db.exec(`INSERT INTO users (id, name, email) VALUES ('u3', 'Late', '${NEW}')`);
    await expectError(verifyNewEmailCode(deps, actor, "222222"), "email_taken");
    expect((await user()).email).toBe(OLD);
    expect(billing.calls).toEqual([]);
    expect((await getEmailChangeState(deps, actor)).request).toMatchObject({ stage: "enter_new", newEmail: null });
  });

  it("undoes the billing email when the database refuses the change", async () => {
    await toNewEmailStep();
    await submitNewEmail(deps, actor, NEW);
    // Another account takes the address while Stripe is being updated.
    const realUpdate = deps.updateBillingEmail;
    deps.updateBillingEmail = async (...args) => {
      const undo = await realUpdate(...args);
      await db.exec(`INSERT INTO users (id, name, email) VALUES ('u3', 'Race', '${NEW}')`);
      return undo;
    };
    await expectError(verifyNewEmailCode(deps, actor, "222222"), "email_taken");
    expect(billing.undone).toBe(1);
    expect((await user()).email).toBe(OLD);
    expect((await db.query("SELECT id FROM session WHERE \"userId\" = 'u1'")).rows).toHaveLength(2);
  });

  it("keeps the old email when Stripe can't be updated, and retries without new codes", async () => {
    await toNewEmailStep();
    await submitNewEmail(deps, actor, NEW);
    billing.fail = true;
    await expectError(verifyNewEmailCode(deps, actor, "222222"), "billing_update_failed");
    expect(await user()).toEqual({ email: OLD, emailVerified: true });
    expect((await db.query("SELECT id FROM session WHERE \"userId\" = 'u1'")).rows).toHaveLength(2);
    expect(sent.some((m) => m.kind === "changed")).toBe(false);
    expect((await getEmailChangeState(deps, actor)).request?.stage).toBe("finishing");

    billing.fail = false;
    expect(await finishEmailChange(deps, actor)).toEqual({ email: NEW, sessionsRevoked: 1 });
    expect((await user()).email).toBe(NEW);
  });

  it("finishes even when the notice to the old address fails", async () => {
    await toNewEmailStep();
    await submitNewEmail(deps, actor, NEW);
    deps.sendEmail = async (_userId, message) => {
      if (message.kind === "changed") throw new Error("SES down");
    };
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    expect((await verifyNewEmailCode(deps, actor, "222222")).email).toBe(NEW);
  });

  it("lets the user ask again at once when a code couldn't be sent", async () => {
    deps.sendEmail = async () => {
      throw new Error("SES down");
    };
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    await expectError(startEmailChange(deps, actor, { password: "correct horse" }), "send_failed");
    deps.sendEmail = async (_userId, message) => {
      sent.push(message);
    };
    await startEmailChange(deps, actor, { password: "correct horse" });
    expect(sent).toEqual([{ kind: "current_code", code: "222222" }]);
  });

  it("forgets the request once the email changed some other way", async () => {
    await toNewEmailStep();
    await db.exec(`UPDATE users SET email = 'elsewhere@example.com' WHERE id = 'u1'`);
    expect((await getEmailChangeState(deps, actor)).request).toBeNull();
    expect((await db.query("SELECT * FROM email_change_requests")).rows).toHaveLength(0);
  });

  it("runs one finish at a time, so a second one can't undo the first one's billing change", async () => {
    await toNewEmailStep();
    await submitNewEmail(deps, actor, NEW);
    billing.fail = true;
    await expectError(verifyNewEmailCode(deps, actor, "222222"), "billing_update_failed");
    billing.fail = false;
    // "Try again" sent twice (double click, two tabs of the same session).
    const realUpdate = deps.updateBillingEmail;
    deps.updateBillingEmail = async (...args) => {
      await new Promise((resolve) => setTimeout(resolve, 20));
      return realUpdate(...args);
    };
    const [first, second] = await Promise.allSettled([finishEmailChange(deps, actor), finishEmailChange(deps, actor)]);
    expect(first).toMatchObject({ status: "fulfilled", value: { email: NEW } });
    expect(second).toMatchObject({ status: "rejected", reason: { code: "in_progress" } });
    expect(billing.calls).toEqual([[OLD, NEW]]);
    expect(billing.undone).toBe(0);
    expect((await user()).email).toBe(NEW);
  });

  it("frees the finish for a retry after a failure, and after a crashed one's lease runs out", async () => {
    await toNewEmailStep();
    await submitNewEmail(deps, actor, NEW);
    billing.fail = true;
    await expectError(verifyNewEmailCode(deps, actor, "222222"), "billing_update_failed");
    const claim = (await db.query<{ finish_claimed_at: Date | null }>("SELECT finish_claimed_at FROM email_change_requests")).rows[0];
    expect(claim.finish_claimed_at).toBeNull();

    // A finish that died mid-way leaves its claim behind.
    await db.query("UPDATE email_change_requests SET finish_claimed_at = $1", [clock]);
    billing.fail = false;
    await expectError(finishEmailChange(deps, actor), "in_progress");
    advance(EMAIL_CHANGE_LIMITS.finishLeaseMs + 1);
    expect((await finishEmailChange(deps, actor)).email).toBe(NEW);
  });

  it("limits parallel password checks like sequential ones", async () => {
    let checks = 0;
    deps.verifyPassword = async () => {
      checks += 1;
      await new Promise((resolve) => setTimeout(resolve, 5));
      return false;
    };
    const attempts = await Promise.allSettled(
      Array.from({ length: 12 }, () => startEmailChange(deps, actor, { password: "guess" })),
    );
    expect(attempts.every((a) => a.status === "rejected")).toBe(true);
    expect(checks).toBeLessThanOrEqual(EMAIL_CHANGE_LIMITS.password.perUser);
  });

  it("doesn't count a correct password against the limit", async () => {
    for (let i = 0; i < EMAIL_CHANGE_LIMITS.send.perUser; i += 1) {
      await startEmailChange(deps, actor, { password: "correct horse" });
      advance(EMAIL_CHANGE_LIMITS.resendCooldownMs);
    }
    const rows = (await db.query("SELECT id FROM email_change_events WHERE action = 'password'")).rows;
    expect(rows).toHaveLength(0);
  });

  it("limits parallel codes to different new addresses like sequential ones", async () => {
    await toNewEmailStep();
    await submitNewEmail(deps, actor, "first@example.com");
    const sends = await Promise.allSettled(
      Array.from({ length: 12 }, (_, i) => submitNewEmail(deps, actor, `target${i}@example.com`)),
    );
    // The current-address code and the first new one already used two sends this hour.
    expect(sends.filter((s) => s.status === "fulfilled").length).toBeLessThanOrEqual(EMAIL_CHANGE_LIMITS.send.perUser - 2);
    expect(sent.filter((m) => m.kind === "new_code").length).toBeLessThanOrEqual(EMAIL_CHANGE_LIMITS.send.perUser - 1);
    // Refused attempts give their slot back.
    expect((await submitNewEmail(deps, actor, NEW)).request?.newEmail).toBe(NEW);
  });

  it("limits codes to one new address across accounts and networks", async () => {
    const target = "victim@example.com";
    let sentToTarget = 0;
    for (let i = 0; i < EMAIL_CHANGE_LIMITS.send.perTarget + 2; i += 1) {
      const id = `x${i}`;
      await db.query(`INSERT INTO users (id, name, email, "emailVerified") VALUES ($1, 'X', $2, true)`, [id, `${id}@example.com`]);
      await db.query(
        `INSERT INTO session (id, "expiresAt", token, "createdAt", "updatedAt", "userId") VALUES ($1, now() + interval '1 day', $1, now(), now(), $2)`,
        [`s-${id}`, id],
      );
      const attacker = { userId: id, sessionId: `s-${id}`, sessionCreatedAt: clock, ip: `198.51.100.${i}` };
      codes.unshift("101010");
      await db.exec("DELETE FROM account");
      await startEmailChange(deps, attacker, {});
      await verifyCurrentEmailCode(deps, attacker, "101010");
      const result = await submitNewEmail(deps, attacker, target).then(
        () => "sent",
        (e: EmailChangeError) => e.code,
      );
      if (result === "sent") sentToTarget += 1;
      else expect(result).toBe("rate_limited");
    }
    expect(sentToTarget).toBe(EMAIL_CHANGE_LIMITS.send.perTarget);
  });

  it("counts lookups of addresses in use against the send limit", async () => {
    await toNewEmailStep();
    const results: string[] = [];
    for (let i = 0; i < EMAIL_CHANGE_LIMITS.send.perUser; i += 1) {
      await db.query(`INSERT INTO users (id, name, email) VALUES ($1, 'Taken', $2)`, [`t${i}`, `taken${i}@example.com`]);
      results.push(await submitNewEmail(deps, actor, `taken${i}@example.com`).then(() => "sent", (e: EmailChangeError) => e.code));
    }
    // One send went to the current address; the rest of the hour's budget is spent on lookups.
    expect(results).toEqual([
      ...Array(EMAIL_CHANGE_LIMITS.send.perUser - 1).fill("email_taken"),
      "rate_limited",
    ]);
  });

  it("refuses addresses Better Auth couldn't sign in with", async () => {
    await toNewEmailStep();
    for (const address of ["a!b@example.com", "dé@example.com", "a..b@example.com", "x@localhost", "x@example.c0m"]) {
      await expectError(submitNewEmail(deps, actor, address), "invalid_email");
    }
    expect((await submitNewEmail(deps, actor, "first.o'neil+clips@sub.example.co")).request?.newEmail).toBe(
      "first.o'neil+clips@sub.example.co",
    );
  });

  it("removes the request with the account", async () => {
    await startEmailChange(deps, actor, { password: "correct horse" });
    await db.exec("DELETE FROM users WHERE id = 'u1'");
    expect((await db.query("SELECT * FROM email_change_requests")).rows).toHaveLength(0);
    expect((await db.query("SELECT * FROM email_change_events")).rows).toHaveLength(0);
  });

  it("removes the change log with the account", async () => {
    await toNewEmailStep();
    await submitNewEmail(deps, actor, NEW);
    await verifyNewEmailCode(deps, actor, "222222");
    await db.exec("DELETE FROM users WHERE id = 'u1'");
    expect((await db.query("SELECT * FROM email_change_log")).rows).toHaveLength(0);
  });
});

it.each([false, true])("both migration runners create the tables, in either order (backend first: %s)", async (backendFirst) => {
  const db = new PGlite();
  try {
    await db.exec("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY)");
    const migrations = [
      read("../../prisma/migrations/20261119120000_email_change_requests/migration.sql"),
      read("../../../backend/src/migrations/sql/20261119_0001_email_change_requests.sql"),
    ];
    if (backendFirst) migrations.reverse();
    for (const migration of migrations) await db.exec(migration);
    const tables = await db.query<{ table_name: string }>(
      "SELECT table_name FROM information_schema.tables WHERE table_name LIKE 'email_change_%' ORDER BY table_name",
    );
    expect(tables.rows.map((row) => row.table_name)).toEqual(["email_change_events", "email_change_log", "email_change_requests"]);
  } finally {
    await db.close();
  }
}, 30_000);
