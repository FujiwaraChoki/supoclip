import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const TEAM = {
  id: "team_example_1", name: "Northwind Studio", slug: "northwind", ownerId: "user_example_1", role: "admin",
  plan: "team", subscriptionStatus: "active", seatLimit: 3, memberCount: 2, pendingInviteCount: 0, occupiedSeats: 2,
};

type Hook = typeof import("./use-workspace");
let hook: Hook;
let teamsResponse: () => Response;

beforeEach(async () => {
  // The hook keeps the last answer for the whole tab; start each test from a fresh tab.
  vi.resetModules();
  hook = await import("./use-workspace");
  teamsResponse = () => Response.json({ activeTeamId: TEAM.id, teams: [TEAM] });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).includes("/api/teams")) return teamsResponse();
    return Response.json({});
  }));
});

afterEach(() => vi.unstubAllGlobals());

describe("useWorkspace", () => {
  it("keeps the teams it knows when a refresh fails", async () => {
    const { result } = renderHook(() => hook.useWorkspace(true));
    await waitFor(() => expect(result.current.teams).toHaveLength(1));

    teamsResponse = () => new Response("upstream error", { status: 502 });
    await act(async () => { await result.current.refresh(); });
    expect(result.current.activeTeam?.name).toBe("Northwind Studio");

    vi.mocked(fetch).mockRejectedValueOnce(new TypeError("network down"));
    await act(async () => { await result.current.refresh(); });
    expect(result.current.teams).toHaveLength(1);
  });

  it("forgets them once nobody is signed in", async () => {
    const { result } = renderHook(() => hook.useWorkspace(true));
    await waitFor(() => expect(result.current.teams).toHaveLength(1));

    teamsResponse = () => Response.json({ error: "Unauthorized" }, { status: 401 });
    await act(async () => { await result.current.refresh(); });
    expect(result.current.teams).toEqual([]);
    expect(result.current.activeTeamId).toBeNull();
  });

  it("is loading from the render where it is switched on, not one render later", async () => {
    const { result, rerender } = renderHook(({ enabled }) => hook.useWorkspace(enabled), { initialProps: { enabled: false } });
    expect(result.current.loading).toBe(false);

    rerender({ enabled: true });
    // Before any effect has answered: "no teams" is not known yet.
    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.teams).toHaveLength(1);
  });

  it("hides team creation only when the server says it is unavailable", async () => {
    const { result } = renderHook(() => hook.useWorkspace(true));
    expect(result.current.teamsAvailable).toBe(true);
    await waitFor(() => expect(result.current.teams).toHaveLength(1));
    expect(result.current.teamsAvailable).toBe(true);

    teamsResponse = () => Response.json({ activeTeamId: null, teams: [TEAM], available: false });
    await act(async () => { await result.current.refresh(); });
    expect(result.current.teamsAvailable).toBe(false);
    expect(result.current.teams).toHaveLength(1);
  });

  it("passes the server's error code on and leaves the wording to the caller", async () => {
    const { result } = renderHook(() => hook.useWorkspace(false));
    vi.mocked(fetch).mockResolvedValueOnce(Response.json({ error: "Team workspaces aren't available yet.", code: "teams_unavailable" }, { status: 503 }));
    await expect(result.current.createTeam("Northwind")).rejects.toMatchObject({ code: "teams_unavailable" });

    vi.mocked(fetch).mockResolvedValueOnce(new Response("", { status: 500 }));
    await expect(result.current.switchWorkspace(TEAM.id)).rejects.toMatchObject({ message: "" });
  });
});
