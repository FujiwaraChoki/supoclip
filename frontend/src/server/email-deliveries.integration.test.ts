// @vitest-environment node
import { readFileSync } from "node:fs";
import { PGlite } from "@electric-sql/pglite";
import { expect, it } from "vitest";

it("keeps SES recipient correlation across both migration runners without storing message bodies", async () => {
  const db = new PGlite();
  try {
    const frontend = readFileSync("prisma/migrations/20261016120000_add_email_deliveries/migration.sql", "utf8");
    const backend = readFileSync("../backend/src/migrations/sql/20261016_0001_email_deliveries.sql", "utf8");
    await db.exec(frontend);
    await db.exec(backend);
    await db.query("INSERT INTO email_deliveries (message_id,recipient,subject,region) VALUES ($1,$2,$3,$4)",
      ["ses-complaint-message-id", "owner@example.com", "A new SupoClip API key was created", "us-east-1"]);
    const result = await db.query<{ recipient: string; sent_at: Date }>("SELECT recipient,sent_at FROM email_deliveries WHERE message_id=$1", ["ses-complaint-message-id"]);
    expect(result.rows[0].recipient).toBe("owner@example.com");
    expect(result.rows[0].sent_at).toBeTruthy();
    const columns = await db.query<{ column_name: string }>("SELECT column_name FROM information_schema.columns WHERE table_name='email_deliveries'");
    expect(columns.rows.map((row) => row.column_name).sort()).toEqual(["message_id", "recipient", "region", "sent_at", "subject"]);
  } finally {
    await db.close();
  }
  // Starting PGlite alone can exceed the 5s default on CI runners.
}, 30_000);
