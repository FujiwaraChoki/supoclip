import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";

import { EmailChangeSection } from "./email-change-section";

vi.mock("@/lib/datafast", () => ({ track: vi.fn() }));

const OLD = "owner@example.com";

function state(request: Record<string, unknown> | null, overrides: Record<string, unknown> = {}) {
  return {
    email: OLD,
    emailVerified: true,
    hasPassword: true,
    request: request && {
      newEmail: null,
      codeExpiresAt: "2099-01-01T00:00:00.000Z",
      resendAvailableAt: "2000-01-01T00:00:00.000Z",
      expiresAt: "2099-01-01T00:00:00.000Z",
      ...request,
    },
    ...overrides,
  };
}

type Reply = { status?: number; body: unknown };

function mockApi(replies: Record<string, Reply | Reply[]>) {
  const calls: { method: string; body: Record<string, unknown> | null }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    calls.push({ method, body });
    const key = method === "POST" ? String(body.action) : method;
    const queue = replies[key];
    const reply = Array.isArray(queue) ? queue.shift()! : queue;
    return Response.json(reply.body, { status: reply.status ?? 200 });
  }));
  return calls;
}

async function open() {
  fireEvent.click(screen.getByRole("button", { name: "Change email" }));
}

describe("EmailChangeSection", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("walks through both codes and reports the new address", async () => {
    const onChanged = vi.fn();
    const calls = mockApi({
      GET: { body: state(null) },
      start: { body: state({ stage: "verify_current" }) },
      verify_current: { body: state({ stage: "enter_new" }) },
      new_email: { body: state({ stage: "verify_new", newEmail: "fresh@example.com" }) },
      verify_new: { body: { done: { email: "fresh@example.com", sessionsRevoked: 2 } } },
    });
    render(<EmailChangeSection email={OLD} emailVerified onChanged={onChanged} />);
    expect(screen.getByText("Verified")).toBeInTheDocument();
    await open();

    fireEvent.change(await screen.findByLabelText("Current password"), { target: { value: "secret" } });
    fireEvent.click(screen.getByRole("button", { name: "Send code" }));

    expect(await screen.findByText(/Enter the 6-digit code we sent to/)).toHaveTextContent(OLD);
    fireEvent.change(screen.getByLabelText("Code"), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Verify" }));

    fireEvent.change(await screen.findByLabelText("New email address"), { target: { value: "fresh@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Send code" }));

    expect(await screen.findByText(/Enter the 6-digit code we sent to/)).toHaveTextContent("fresh@example.com");
    fireEvent.change(screen.getByLabelText("Code"), { target: { value: "654321" } });
    fireEvent.click(screen.getByRole("button", { name: "Change email" }));

    expect(await screen.findByText("Email changed")).toBeInTheDocument();
    expect(screen.getByText(/We signed you out on your other devices and sent a notice to owner@example.com/)).toBeInTheDocument();
    expect(onChanged).toHaveBeenCalledWith("fresh@example.com");
    expect(calls.filter((c) => c.method === "POST").map((c) => c.body)).toEqual([
      { action: "start", password: "secret" },
      { action: "verify_current", code: "123456" },
      { action: "new_email", email: "fresh@example.com" },
      { action: "verify_new", code: "654321" },
    ]);
  });

  it("explains a never-verified address and skips the password when there is none", async () => {
    mockApi({ GET: { body: state(null, { emailVerified: false, hasPassword: false }) } });
    render(<EmailChangeSection email={OLD} emailVerified={false} />);
    expect(screen.getByText("Not verified")).toBeInTheDocument();
    await open();
    expect(await screen.findByText(/isn't verified yet/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Current password")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send code" })).toBeEnabled();
  });

  it("resumes an open request and shows the attempts left on a wrong code", async () => {
    mockApi({
      GET: { body: state({ stage: "verify_current" }) },
      verify_current: { status: 400, body: { error: "wrong_code", attemptsLeft: 2 } },
    });
    render(<EmailChangeSection email={OLD} emailVerified />);
    await open();
    fireEvent.change(await screen.findByLabelText("Code"), { target: { value: "000000" } });
    fireEvent.click(screen.getByRole("button", { name: "Verify" }));
    expect(await screen.findByText("That code isn't right. 2 tries left.")).toBeInTheDocument();
  });

  it("holds the resend button during the cooldown", async () => {
    mockApi({ GET: { body: state({ stage: "verify_current", resendAvailableAt: new Date(Date.now() + 45_000).toISOString() }) } });
    render(<EmailChangeSection email={OLD} emailVerified />);
    await open();
    expect(await screen.findByRole("button", { name: /Send a new code in \d+s/ })).toBeDisabled();
  });

  it("says when the address belongs to another account", async () => {
    mockApi({
      GET: { body: state({ stage: "enter_new" }) },
      new_email: { status: 409, body: { error: "email_taken" } },
    });
    render(<EmailChangeSection email={OLD} emailVerified />);
    await open();
    fireEvent.change(await screen.findByLabelText("New email address"), { target: { value: "taken@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Send code" }));
    expect(await screen.findByText("Another account already uses that email address.")).toBeInTheDocument();
    expect(screen.getByLabelText("New email address")).toBeInTheDocument();
  });

  it("offers a retry when billing couldn't be updated", async () => {
    const calls = mockApi({
      GET: { body: state({ stage: "verify_new", newEmail: "fresh@example.com" }) },
      verify_new: { status: 502, body: { error: "billing_update_failed" } },
      finish: { body: { done: { email: "fresh@example.com", sessionsRevoked: 0 } } },
    });
    render(<EmailChangeSection email={OLD} emailVerified />);
    await open();
    fireEvent.change(await screen.findByLabelText("Code"), { target: { value: "654321" } });
    fireEvent.click(screen.getByRole("button", { name: "Change email" }));
    expect(await screen.findByText(/couldn't update your billing email, so nothing changed/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Email changed")).toBeInTheDocument();
    expect(calls.at(-1)?.body).toEqual({ action: "finish" });
  });

  it("picks up the new address when another finish was already saving it", async () => {
    const onChanged = vi.fn();
    mockApi({
      GET: [
        { body: state({ stage: "finishing", newEmail: "fresh@example.com" }) },
        { body: state(null, { email: "fresh@example.com" }) },
      ],
      finish: { status: 409, body: { error: "in_progress" } },
    });
    render(<EmailChangeSection email={OLD} emailVerified onChanged={onChanged} />);
    await open();
    fireEvent.click(await screen.findByRole("button", { name: "Try again" }));
    expect(await screen.findByText(/already being saved/)).toBeInTheDocument();
    await waitFor(() => expect(onChanged).toHaveBeenCalledWith("fresh@example.com"));
  });

  it("shows how long to wait when rate limited", async () => {
    mockApi({
      GET: { body: state(null) },
      start: { status: 429, body: { error: "rate_limited", retryAfterSeconds: 1500 } },
    });
    render(<EmailChangeSection email={OLD} emailVerified />);
    await open();
    fireEvent.change(await screen.findByLabelText("Current password"), { target: { value: "secret" } });
    fireEvent.click(screen.getByRole("button", { name: "Send code" }));
    await waitFor(() => expect(screen.getByText("Too many attempts. Try again in 25 min.")).toBeInTheDocument());
  });
});
