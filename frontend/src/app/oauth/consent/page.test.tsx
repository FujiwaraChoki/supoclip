import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import OAuthConsentPage from "./page";

const mocks = vi.hoisted(() => ({
  session: { user: { id: "user-1", email: "owner@example.com" } } as unknown,
  replace: vi.fn(),
  assign: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mocks.replace, push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(window.location.search),
}));
vi.mock("@/lib/auth-client", () => ({
  useSession: () => ({ data: mocks.session, isPending: false }),
}));

const REQUEST = {
  client_name: "Claude",
  redirect_host: "claude.ai",
  email_verified: true,
  at_key_limit: false,
  max_keys: 25,
};

function respond(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

beforeEach(() => {
  vi.resetAllMocks();
  mocks.session = { user: { id: "user-1", email: "owner@example.com" } };
  window.history.replaceState(null, "", "/oauth/consent?request=req-1");
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { ...window.location, assign: mocks.assign, pathname: "/oauth/consent", search: "?request=req-1" },
  });
});

it("sends signed-out users to sign in and back", async () => {
  mocks.session = null;
  vi.stubGlobal("fetch", vi.fn());
  render(<OAuthConsentPage />);
  await waitFor(() => expect(mocks.replace).toHaveBeenCalledWith("/sign-in?next=%2Foauth%2Fconsent%3Frequest%3Dreq-1"));
  expect(fetch).not.toHaveBeenCalled();
});

it("shows the client, what it can do, and allows it", async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(respond(200, REQUEST))
    .mockResolvedValueOnce(respond(200, { redirect_url: "https://claude.ai/api/mcp/auth_callback?code=abc&state=s" }));
  vi.stubGlobal("fetch", fetchMock);
  render(<OAuthConsentPage />);

  expect(await screen.findByText("Allow Claude to use your SupoClip account?")).toBeInTheDocument();
  expect(screen.getByText("Signed in as owner@example.com.")).toBeInTheDocument();
  expect(screen.getByText("Create and manage clip generations")).toBeInTheDocument();
  expect(screen.getByText("Read your clips and plan usage")).toBeInTheDocument();
  expect(screen.getByText("Spend your plan's video minutes")).toBeInTheDocument();
  expect(screen.getByText("claude.ai")).toBeInTheDocument();
  expect(screen.getByText("Claude connector")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Allow" }));
  await waitFor(() => expect(mocks.assign).toHaveBeenCalledWith("https://claude.ai/api/mcp/auth_callback?code=abc&state=s"));
  expect(fetchMock).toHaveBeenLastCalledWith("/api/oauth/requests/req-1/decision", expect.objectContaining({
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ approve: true }),
  }));
});

it("denies", async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(respond(200, REQUEST))
    .mockResolvedValueOnce(respond(200, { redirect_url: "https://claude.ai/cb?error=access_denied" }));
  vi.stubGlobal("fetch", fetchMock);
  render(<OAuthConsentPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Deny" }));
  await waitFor(() => expect(mocks.assign).toHaveBeenCalledWith("https://claude.ai/cb?error=access_denied"));
  expect(fetchMock.mock.calls[1][1].body).toBe(JSON.stringify({ approve: false }));
});

it("blocks Allow at the key limit and links to settings", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(respond(200, { ...REQUEST, at_key_limit: true })));
  render(<OAuthConsentPage />);
  expect(await screen.findByText(/You already have 25 active API keys/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Allow" })).toBeDisabled();
  expect(screen.getAllByRole("link", { name: "Agents & API" })[0]).toHaveAttribute("href", "/settings/api-keys");
});

it("blocks Allow until the email is verified", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(respond(200, { ...REQUEST, email_verified: false })));
  render(<OAuthConsentPage />);
  expect(await screen.findByText(/Verify your email address before connecting an app/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Allow" })).toBeDisabled();
});

it("explains an expired request", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(respond(404, { detail: { code: "request_expired", message: "x" } })));
  render(<OAuthConsentPage />);
  expect(await screen.findByText("This request has expired")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Allow" })).not.toBeInTheDocument();
});

it("shows a key limit hit at decision time", async () => {
  vi.stubGlobal("fetch", vi.fn()
    .mockResolvedValueOnce(respond(200, REQUEST))
    .mockResolvedValueOnce(respond(409, { detail: { code: "key_limit", message: "x" } })));
  render(<OAuthConsentPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Allow" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("You already have 25 active API keys");
  expect(mocks.assign).not.toHaveBeenCalled();
});
