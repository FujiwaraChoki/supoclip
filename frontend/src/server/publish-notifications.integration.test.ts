// @vitest-environment node
import { readFileSync } from "node:fs";
import { PGlite } from "@electric-sql/pglite";
import { expect, it } from "vitest";

it.each([false, true])(
  "adds publish notification columns idempotently when both migration runners execute (backend first: %s)",
  async (backendFirst) => {
    const db = new PGlite();
    try {
      await db.exec(`
        CREATE TABLE users (id text PRIMARY KEY, notify_on_completion boolean NOT NULL DEFAULT true);
        CREATE TABLE social_posts (id text PRIMARY KEY, user_id text NOT NULL REFERENCES users(id));
      `);
      const migrations = [
        readFileSync("prisma/migrations/20261120120000_add_publish_notifications/migration.sql", "utf8"),
        readFileSync("../backend/src/migrations/sql/20261120_0001_publish_notifications.sql", "utf8"),
      ];
      if (backendFirst) migrations.reverse();

      await db.exec(migrations[0]);
      await db.exec("INSERT INTO users (id) VALUES ('user-1')");
      await db.exec("INSERT INTO social_posts (id, user_id) VALUES ('post-1', 'user-1')");

      const defaults = await db.query<{ notify_on_publish: boolean }>(
        "SELECT notify_on_publish FROM users WHERE id = 'user-1'",
      );
      expect(defaults.rows[0].notify_on_publish).toBe(false);

      const postDefault = await db.query<{ publish_notification_sent_at: Date | null }>(
        "SELECT publish_notification_sent_at FROM social_posts WHERE id = 'post-1'",
      );
      expect(postDefault.rows[0].publish_notification_sent_at).toBeNull();

      await db.exec("UPDATE users SET notify_on_publish = true WHERE id = 'user-1'");
      await db.exec(
        "UPDATE social_posts SET publish_notification_sent_at = '2026-11-20T12:00:00Z' WHERE id = 'post-1'",
      );

      await db.exec(migrations[1]);

      const userAfter = await db.query<{ notify_on_publish: boolean; notify_on_completion: boolean }>(
        "SELECT notify_on_publish, notify_on_completion FROM users WHERE id = 'user-1'",
      );
      expect(userAfter.rows[0]).toEqual({ notify_on_publish: true, notify_on_completion: true });

      const postAfter = await db.query<{ publish_notification_sent_at: Date }>(
        "SELECT publish_notification_sent_at FROM social_posts WHERE id = 'post-1'",
      );
      expect(postAfter.rows[0].publish_notification_sent_at.toISOString()).toBe(
        "2026-11-20T12:00:00.000Z",
      );
    } finally {
      await db.close();
    }
  },
  30_000,
);
