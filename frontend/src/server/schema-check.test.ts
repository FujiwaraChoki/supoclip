// @vitest-environment node
import { describe, expect, it } from "vitest";
import { findMissingColumns } from "./schema-check";

const expected = [
  { table: "users", columns: ["id", "email", "stripe_account_id"] },
  { table: "social_posts", columns: ["id"] },
];

describe("findMissingColumns", () => {
  it("is empty when every table and column exists", () => {
    const present = [
      { table_name: "users", column_name: "id" },
      { table_name: "users", column_name: "email" },
      { table_name: "users", column_name: "stripe_account_id" },
      { table_name: "social_posts", column_name: "id" },
    ];
    expect(findMissingColumns(expected, present)).toEqual([]);
  });

  it("names a missing column and a missing table", () => {
    const present = [
      { table_name: "users", column_name: "id" },
      { table_name: "users", column_name: "email" },
    ];
    expect(findMissingColumns(expected, present)).toEqual(["users.stripe_account_id", "social_posts"]);
  });
});
