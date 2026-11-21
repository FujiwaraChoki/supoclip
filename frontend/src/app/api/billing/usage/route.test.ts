// @vitest-environment node
import { beforeEach, expect, it, vi } from "vitest";

const sessionMock = vi.fn();
const fetchBackendMock = vi.fn();
const findManyMock = vi.fn();
const activeTeamMock = vi.fn();

vi.mock("@/server/session", () => ({ getServerSession: () => sessionMock() }));
vi.mock("@/server/prisma", () => ({ getPrismaClient: () => ({ teamMember: { findMany: findManyMock } }) }));
vi.mock("@/server/backend-api", () => ({ fetchBackend: (...args: unknown[]) => fetchBackendMock(...args) }));
vi.mock("@/server/workspace", () => ({ getVerifiedActiveTeamId: (...args: unknown[]) => activeTeamMock(...args) }));

import { GET } from "./route";

const TEAM_ID = "team_example_1";

beforeEach(() => {
  vi.clearAllMocks();
  sessionMock.mockResolvedValue({ user: { id: "user_example_1" } });
  findManyMock.mockResolvedValue([{ role: "member", team: { id: TEAM_ID, name: "Northwind Studio" } }]);
  activeTeamMock.mockResolvedValue(TEAM_ID);
  fetchBackendMock.mockImplementation(async (_path: string, init: { teamId: string | null }) =>
    Response.json(init.teamId
      ? { workspace_type: "team", team_id: init.teamId, plan: "team", minute_limit: 3600, usage_minutes: 1840 }
      : { workspace_type: "personal", plan: "pro", minute_limit: 600, usage_minutes: 312 }),
  );
});

it("returns personal usage and every team's usage, each fetched for its own workspace", async () => {
  const response = await GET();
  expect(response.status).toBe(200);
  const body = await response.json();

  expect(body.activeTeamId).toBe(TEAM_ID);
  expect(body.workspaces).toEqual([
    { teamId: null, name: null, role: null, summary: expect.objectContaining({ plan: "pro", usage_minutes: 312 }) },
    { teamId: TEAM_ID, name: "Northwind Studio", role: "member", summary: expect.objectContaining({ plan: "team", usage_minutes: 1840 }) },
  ]);
  expect(fetchBackendMock).toHaveBeenCalledWith("/tasks/billing/summary", expect.objectContaining({ userId: "user_example_1", teamId: null }));
  expect(fetchBackendMock).toHaveBeenCalledWith("/tasks/billing/summary", expect.objectContaining({ userId: "user_example_1", teamId: TEAM_ID }));
  expect(findManyMock).toHaveBeenCalledWith(expect.objectContaining({ where: { user_id: "user_example_1" } }));
});

it("leaves out a workspace whose summary fails", async () => {
  fetchBackendMock.mockImplementation(async (_path: string, init: { teamId: string | null }) =>
    init.teamId ? new Response("forbidden", { status: 403 }) : Response.json({ workspace_type: "personal", plan: "pro" }),
  );
  const body = await (await GET()).json();
  expect(body.workspaces.map((item: { teamId: string | null }) => item.teamId)).toEqual([null]);
});

it("rejects signed-out callers", async () => {
  sessionMock.mockResolvedValue(null);
  const response = await GET();
  expect(response.status).toBe(401);
  expect(fetchBackendMock).not.toHaveBeenCalled();
});
