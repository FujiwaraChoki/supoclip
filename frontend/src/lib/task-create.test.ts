import { describe, expect, it } from "vitest";

import { taskPathFromCreate } from "./task-create";

describe("taskPathFromCreate", () => {
  it("opens a new generation", () => {
    expect(taskPathFromCreate({ task_id: "task-1" })).toBe("/tasks/task-1");
  });

  it("opens the generation already in progress", () => {
    expect(taskPathFromCreate({ task_id: "task-1", duplicate: true })).toBe("/tasks/task-1?already=1");
    expect(taskPathFromCreate({ task_id: "task-1", code: "DUPLICATE_SOURCE" })).toBe(
      "/tasks/task-1?already=1",
    );
  });
});
