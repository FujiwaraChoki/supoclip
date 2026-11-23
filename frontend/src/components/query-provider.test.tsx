import { useQuery, useQueryClient } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import { memo } from "react";
import { beforeEach, expect, it, vi } from "vitest";

import { WORKSPACE_SWITCHED_EVENT } from "@/hooks/use-workspace";
import { QueryScope } from "./query-provider";

const session = vi.hoisted(() => ({ current: { data: { user: { id: "user-a" } } as unknown, isPending: false } }));
vi.mock("@/lib/auth-client", () => ({ useSession: () => session.current }));

beforeEach(() => {
  session.current = { data: { user: { id: "user-a" } }, isPending: false };
});

function Probe({ queryKey }: { queryKey: string }) {
  const queryClient = useQueryClient();
  const { data } = useQuery({ queryKey: [queryKey], queryFn: async () => `${queryKey}-fetched` });
  return <p data-testid={queryKey}>{String(data)} {String(queryClient.getQueryData(["inactive"]) ?? "none")}</p>;
}

it("drops everything cached for the previous user when another account signs in", async () => {
  const view = render(<><QueryScope /><Probe queryKey="tasks" /></>);
  await waitFor(() => expect(screen.getByTestId("tasks")).toHaveTextContent("tasks-fetched"));

  let client!: ReturnType<typeof useQueryClient>;
  function Grab() { client = useQueryClient(); return null; }
  view.rerender(<><QueryScope /><Probe queryKey="tasks" /><Grab /></>);
  act(() => { client.setQueryData(["private"], "user-a data"); });

  session.current = { data: { user: { id: "user-b" } }, isPending: false };
  view.rerender(<><QueryScope /><Probe queryKey="tasks" /><Grab /></>);
  await waitFor(() => expect(client.getQueryData(["private"])).toBeUndefined());
});

it("refetches a query already on screen as the new user instead of keeping the old answer", async () => {
  let user = "user-a";
  function Mine() {
    const { data } = useQuery({ queryKey: ["billing"], queryFn: async () => `plan of ${user}` });
    return <p data-testid="mine">{data ?? "loading"}</p>;
  }
  // Memoized like a page that has no reason to re-render when the session changes.
  const MemoMine = memo(Mine);
  const view = render(<><QueryScope /><MemoMine /></>);
  await waitFor(() => expect(screen.getByTestId("mine")).toHaveTextContent("plan of user-a"));

  user = "user-b";
  session.current = { data: { user: { id: "user-b" } }, isPending: false };
  view.rerender(<><QueryScope /><MemoMine /></>);
  await waitFor(() => expect(screen.getByTestId("mine")).toHaveTextContent("plan of user-b"));
});

it("keeps the cache while the session first loads", async () => {
  session.current = { data: null, isPending: true };
  let client!: ReturnType<typeof useQueryClient>;
  function Grab() { client = useQueryClient(); return null; }
  const view = render(<><QueryScope /><Grab /></>);
  act(() => { client.setQueryData(["kept"], "value"); });

  session.current = { data: { user: { id: "user-a" } }, isPending: false };
  view.rerender(<><QueryScope /><Grab /></>);
  expect(client.getQueryData(["kept"])).toBe("value");
});

it("drops data off screen and refetches what is on screen after a workspace switch in another tab", async () => {
  let calls = 0;
  function Live() {
    const { data } = useQuery({ queryKey: ["live"], queryFn: async () => `live-${++calls}` });
    return <p data-testid="live">{data}</p>;
  }
  let client!: ReturnType<typeof useQueryClient>;
  function Grab() { client = useQueryClient(); return null; }
  render(<><QueryScope /><Live /><Grab /></>);
  await waitFor(() => expect(screen.getByTestId("live")).toHaveTextContent("live-1"));
  act(() => { client.setQueryData(["other-workspace-list"], ["old"]); });

  act(() => { window.dispatchEvent(new CustomEvent(WORKSPACE_SWITCHED_EVENT)); });
  expect(client.getQueryData(["other-workspace-list"])).toBeUndefined();
  await waitFor(() => expect(screen.getByTestId("live")).toHaveTextContent("live-2"));
});
