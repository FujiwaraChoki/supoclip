import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { invalidateBillingSummary, useBillingSummary } from "./use-billing-summary";

const SUMMARY = { plan: "pro", usage_minutes: 12, can_create_task: true, upgrade_required: false, reason: null };

afterEach(() => vi.unstubAllGlobals());

it("keeps the last summary when a refresh fails, and forgets it on a 401", async () => {
  let respond = () => Response.json(SUMMARY);
  vi.stubGlobal("fetch", vi.fn(async () => respond()));
  const { result } = renderHook(() => useBillingSummary(true));
  await waitFor(() => expect(result.current[0]?.plan).toBe("pro"));

  respond = () => new Response("bad gateway", { status: 502 });
  await act(async () => { invalidateBillingSummary(); });
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  expect(result.current[0]?.plan).toBe("pro");

  respond = () => Response.json({ error: "Unauthorized" }, { status: 401 });
  await act(async () => { invalidateBillingSummary(); });
  await waitFor(() => expect(result.current[0]).toBeNull());
});

it("refetches once per workspace change however many components show the summary", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json(SUMMARY)));
  const { result } = renderHook(() => [useBillingSummary(true), useBillingSummary(true), useBillingSummary(true)]);
  await waitFor(() => expect(result.current[0][0]?.plan).toBe("pro"));
  expect(fetch).toHaveBeenCalledTimes(1);

  await act(async () => { invalidateBillingSummary(); });
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)); });
  expect(fetch).toHaveBeenCalledTimes(2);
});
