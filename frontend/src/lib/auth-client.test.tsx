import { act } from "react";
import { hydrateRoot, type Root } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

const auth = vi.hoisted(() => ({
  session: {
    data: null as null | { user: { id: string } },
    isPending: true,
    error: null,
    refetch: vi.fn(),
  },
}));

vi.mock("better-auth/react", () => ({
  createAuthClient: () => ({
    signIn: vi.fn(), signOut: vi.fn(), signUp: vi.fn(),
    useSession: () => auth.session,
  }),
}));

import { useSession } from "./auth-client";

function SessionView() {
  const session = useSession();
  return <div>{session.isPending ? "Loading" : session.data ? "Signed in" : "Sign in required"}</div>;
}

let root: Root | undefined;
afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = undefined;
  document.body.innerHTML = "";
});

describe("session hydration", () => {
  it.each([null, { user: { id: "user" } }])("hydrates when a sibling has already resolved the session: %j", async (data) => {
    auth.session = { ...auth.session, data: null, isPending: true };
    const container = document.createElement("div");
    container.innerHTML = renderToString(<SessionView />);
    document.body.append(container);
    expect(container.textContent).toBe("Loading");

    // Better Auth's shared store resolves before this server-rendered tree loads.
    auth.session = { ...auth.session, data, isPending: false };
    const onRecoverableError = vi.fn();
    await act(async () => {
      root = hydrateRoot(container, <SessionView />, { onRecoverableError });
    });

    expect(onRecoverableError).not.toHaveBeenCalled();
    expect(container.textContent).toBe(data ? "Signed in" : "Sign in required");
  });
});
