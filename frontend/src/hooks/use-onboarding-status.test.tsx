import { act, renderHook, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";

import { useAgentStatus } from "./use-agent-status";
import { useOnboardingStatus } from "./use-onboarding-status";

afterEach(() => vi.unstubAllGlobals());

// Home shows its skeleton while either status is `undefined`. The form it then shows mounts more
// readers of the same queries (app shell, guide); they must not send home back to loading.
it("settles on null when the statuses can't be read, and stays settled as more readers mount", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response("bad gateway", { status: 502 })));
  const { result } = renderHook(() => {
    const [more, setMore] = useState(false);
    const onboarding = useOnboardingStatus();
    const agent = useAgentStatus();
    const laterOnboarding = useOnboardingStatus(more);
    const laterAgent = useAgentStatus(more);
    return { onboarding, agent, laterOnboarding, laterAgent, setMore };
  });
  await waitFor(() => expect(result.current.onboarding).toBeNull());
  await waitFor(() => expect(result.current.agent).toBeNull());

  await act(async () => { result.current.setMore(true); });
  expect(result.current.onboarding).toBeNull();
  expect(result.current.agent).toBeNull();
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)); });
  expect(result.current.onboarding).toBeNull();
  expect(result.current.agent).toBeNull();
});
