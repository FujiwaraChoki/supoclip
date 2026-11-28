import {
  agentLimitFromError, agentLimitFromStatus, agentRequestError, answeredReply, firstLink, parseRetryAfter, waitParts, type AgentItem,
} from "./agent";

describe("agent helpers", () => {
  it("finds the video link in a message", () => {
    expect(firstLink("clip this https://youtu.be/abc please")).toBe("https://youtu.be/abc");
    expect(firstLink("no link here")).toBeNull();
  });

  it("treats the next message after an options card as its answer", () => {
    const items: AgentItem[] = [
      { type: "tool", id: "t1", name: "show_options", input: {}, status: "done" },
      { type: "text", id: "x", text: "Pick one" },
      { type: "user", id: "u1", text: "Make a series", attachments: [], created_at: null },
    ];
    expect(answeredReply(items, 0)).toBe("Make a series");
    expect(answeredReply(items.slice(0, 2), 0)).toBeNull();
  });
});

describe("agent limits", () => {
  const response = (status: number, body: unknown, headers: Record<string, string> = {}) =>
    new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", ...headers } });

  it("keeps the code and retry_after from a 429 body", async () => {
    const error = await agentRequestError(response(429, { detail: { code: "AGENT_RATE_LIMITED", message: "Slow down", retry_after: 12 } }, { "retry-after": "40" }));
    expect(error).toMatchObject({ status: 429, code: "AGENT_RATE_LIMITED", retryAfter: 12, message: "Slow down" });
  });

  it("falls back to the Retry-After header (seconds or a date)", async () => {
    const seconds = await agentRequestError(response(429, { detail: { code: "AGENT_DAILY_LIMIT", message: "Done for today" } }, { "retry-after": "7200" }));
    expect(seconds.retryAfter).toBe(7200);
    const noBody = await agentRequestError(new Response("busy", { status: 429, headers: { "retry-after": "5" } }));
    expect(noBody).toMatchObject({ status: 429, code: undefined, retryAfter: 5 });
    expect(parseRetryAfter(new Date(1_000_000 + 30_000).toUTCString(), 1_000_000)).toBe(30);
    expect(parseRetryAfter("soon")).toBeUndefined();
    expect(parseRetryAfter(null)).toBeUndefined();
  });

  it("maps 429 codes to the limit the composer explains", () => {
    expect(agentLimitFromError({ status: 429, code: "AGENT_DAILY_LIMIT", retryAfter: 3600 })).toEqual({ kind: "daily", retryAfter: 3600 });
    expect(agentLimitFromError({ status: 429, code: "AGENT_DAILY_LIMIT" })).toEqual({ kind: "daily", retryAfter: null });
    expect(agentLimitFromError({ status: 429, code: "AGENT_RATE_LIMITED", retryAfter: 12 })).toEqual({ kind: "burst", retryAfter: 12 });
    expect(agentLimitFromError({ status: 429, code: "AGENT_CONCURRENT_LIMIT", retryAfter: 5 })).toEqual({ kind: "concurrent", retryAfter: 5 });
    expect(agentLimitFromError({ status: 429 })).toEqual({ kind: "burst", retryAfter: 10 });
    expect(agentLimitFromError({ status: 402, code: "AGENT_TRIAL_USED" })).toBeNull();
    expect(agentLimitFromError(new Error("network"))).toBeNull();
    expect(agentLimitFromError(null)).toBeNull();
  });

  it("reads the daily cap from the status only when it is reached", () => {
    const base = { available: true, reason: null, home_mode: null, model: "m", daily_limit: 150 } as const;
    expect(agentLimitFromStatus({ ...base, turns_today: 150, daily_retry_after: 900 })).toEqual({ kind: "daily", retryAfter: 900 });
    expect(agentLimitFromStatus({ ...base, turns_today: 3, daily_retry_after: null })).toBeNull();
    expect(agentLimitFromStatus(base)).toBeNull();
    expect(agentLimitFromStatus({ ...base, available: false, reason: "plan", daily_retry_after: 900 })).toBeNull();
  });

  it("rounds waits to hours, minutes, then seconds", () => {
    expect(waitParts(5 * 3600 + 100)).toEqual({ unit: "hours", count: 5 });
    expect(waitParts(3600)).toEqual({ unit: "hours", count: 1 });
    expect(waitParts(3599)).toEqual({ unit: "minutes", count: 60 });
    expect(waitParts(61)).toEqual({ unit: "minutes", count: 2 });
    expect(waitParts(12)).toEqual({ unit: "seconds", count: 12 });
    expect(waitParts(0)).toEqual({ unit: "seconds", count: 1 });
  });
});
