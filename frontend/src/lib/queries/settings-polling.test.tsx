import { act, renderHook } from "@testing-library/react";
import { focusManager, onlineManager } from "@tanstack/react-query";
import { usePollUntil } from "./settings-polling";

describe("usePollUntil", () => {
  afterEach(() => {
    focusManager.setFocused(undefined);
    onlineManager.setOnline(true);
  });

  it("spends attempts only on its interval, not on focus or reconnect", async () => {
    const queryFn = vi.fn(async () => ({ paid: false }));
    const { result } = renderHook(() => usePollUntil({
      queryKey: ["poll-test"],
      queryFn,
      enabled: true,
      done: (value) => value.paid,
      attempts: 3,
      intervalMs: 60_000,
    }));

    await vi.waitFor(() => expect(result.current.answered).toBe(1));
    await act(async () => {
      focusManager.setFocused(false);
      focusManager.setFocused(true);
      onlineManager.setOnline(false);
      onlineManager.setOnline(true);
      await new Promise((resolve) => setTimeout(resolve, 50));
    });

    expect(queryFn).toHaveBeenCalledTimes(1);
    expect(result.current.answered).toBe(1);
    expect(result.current.exhausted).toBe(false);
  });
});
