import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { toast } from "sonner";
import ApiKeysPage from "./page";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), message: vi.fn(), error: vi.fn() } }));

const mocks = vi.hoisted(() => ({
  verified: false,
  send: vi.fn(),
  refetch: vi.fn(),
}));
vi.mock("@/lib/auth-client", () => ({
  useSession: () => ({ data: { user: { id: "user-1", email: "owner@example.com", emailVerified: mocks.verified } }, isPending: false, refetch: mocks.refetch }),
  authClient: { sendVerificationEmail: mocks.send },
}));

beforeEach(() => {
  vi.resetAllMocks();
  vi.unstubAllGlobals();
  mocks.verified = false;
  window.history.replaceState(null, "", "/settings/api-keys");
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ api_keys: [
    { id: "key-1", name: "Existing key", key_prefix: "sk_test", created_at: null, last_used_at: null, revoked: false },
  ] }), { status: 200 })));
});

it("blocks creation but lets unverified users send a link and revoke an existing key", async () => {
  mocks.send.mockResolvedValue({ data: { status: true } });
  render(<ApiKeysPage />);
  expect(screen.getByRole("button", { name: /^Create$/ })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Send verification email" }));
  await screen.findByRole("status");
  expect(mocks.send).toHaveBeenCalledWith({ email: "owner@example.com", callbackURL: "/settings/api-keys" });
  fireEvent.click(await screen.findByRole("button", { name: "Revoke" }));
  await waitFor(() => expect(fetch).toHaveBeenCalledWith("/api/api-keys/key-1", { method: "DELETE" }));
  fireEvent.click(screen.getByRole("button", { name: "I have verified my email" }));
  expect(mocks.refetch).toHaveBeenCalled();
});

it("shows send failures and keeps creation blocked", async () => {
  mocks.send.mockResolvedValue({ error: { message: "Please try again later" } });
  render(<ApiKeysPage />);
  fireEvent.click(screen.getByRole("button", { name: "Send verification email" }));
  await screen.findByText("Please try again later");
  expect(screen.getByRole("button", { name: /^Create$/ })).toBeDisabled();
});

it("shows invalid verification callbacks after loading keys", async () => {
  window.history.replaceState(null, "", "/settings/api-keys?error=TOKEN_EXPIRED");
  render(<ApiKeysPage />);
  await screen.findByText("Existing key");
  expect(screen.getByText(/verification link is invalid or expired/)).toBeInTheDocument();
});

it("allows a verified user to create and copy a key", async () => {
  mocks.verified = true;
  const writeText = vi.fn().mockResolvedValue(undefined);
  vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
  vi.mocked(fetch).mockImplementation(async (_url, options) => new Response(JSON.stringify(
    options?.method === "POST" ? { api_key: { key: "sk_only-once" } } : { api_keys: [] }
  ), { status: 200 }));
  render(<ApiKeysPage />);
  expect(screen.queryByRole("button", { name: "Send verification email" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /^Create$/ }));
  await screen.findByText("sk_only-once");
  fireEvent.click(screen.getByRole("button", { name: "Copy API key" }));
  await waitFor(() => expect(writeText).toHaveBeenCalledWith("sk_only-once"));
  expect(toast.success).toHaveBeenCalledWith("Copied to clipboard");
});

it("selects the new key when the clipboard blocks the copy", async () => {
  mocks.verified = true;
  const writeText = vi.fn().mockRejectedValue(new DOMException("Clipboard permission denied", "NotAllowedError"));
  vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
  document.execCommand = vi.fn().mockReturnValue(false);
  vi.mocked(fetch).mockImplementation(async (_url, options) => new Response(JSON.stringify(
    options?.method === "POST" ? { api_key: { key: "sk_only-once" } } : { api_keys: [] }
  ), { status: 200 }));
  render(<ApiKeysPage />);
  fireEvent.click(screen.getByRole("button", { name: /^Create$/ }));
  const key = await screen.findByText("sk_only-once");
  fireEvent.click(screen.getByRole("button", { name: "Copy API key" }));
  await waitFor(() => expect(toast.message).toHaveBeenCalledWith("Clipboard is blocked. The text is selected so you can copy it manually."));
  expect(writeText).toHaveBeenCalledWith("sk_only-once");
  expect(document.execCommand).toHaveBeenCalledWith("copy");
  expect(window.getSelection()?.toString()).toBe("sk_only-once");
  expect(key.contains(window.getSelection()?.anchorNode ?? null)).toBe(true);
});
