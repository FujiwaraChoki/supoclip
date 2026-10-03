import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import { DeleteAccountSection } from "./delete-account-section";
import { authClient } from "../lib/auth-client";

const push = vi.fn();
const refresh = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, refresh }),
}));

vi.mock("@/lib/datafast", () => ({
  track: vi.fn(),
}));

vi.mock("../lib/auth-client", () => ({
  authClient: {
    deleteUser: vi.fn(),
    signOut: vi.fn(),
  },
}));

async function openDialog() {
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Delete account" }));
  return user;
}

describe("DeleteAccountSection", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(authClient.signOut).mockResolvedValue({ data: { success: true }, error: null } as never);
  });

  it("requires the account email and password before deleting", async () => {
    render(<DeleteAccountSection email="user@example.com" />);
    const user = await openDialog();

    const submit = screen.getByRole("button", { name: "Delete account" });
    expect(submit).toBeDisabled();

    await user.type(screen.getByLabelText(/to confirm/), "wrong@example.com");
    await user.type(screen.getByLabelText("Password"), "Password123!");
    expect(submit).toBeDisabled();

    await user.clear(screen.getByLabelText(/to confirm/));
    await user.type(screen.getByLabelText(/to confirm/), "User@Example.com");
    expect(submit).toBeEnabled();
    expect(authClient.deleteUser).not.toHaveBeenCalled();
  });

  it("deletes the account, signs out and redirects home", async () => {
    vi.mocked(authClient.deleteUser).mockResolvedValue({ data: { success: true }, error: null } as never);
    render(<DeleteAccountSection email="user@example.com" />);
    const user = await openDialog();

    await user.type(screen.getByLabelText(/to confirm/), "user@example.com");
    await user.type(screen.getByLabelText("Password"), "Password123!");
    await user.click(screen.getByRole("button", { name: "Delete account" }));

    expect(authClient.deleteUser).toHaveBeenCalledWith({ password: "Password123!" });
    expect(authClient.signOut).toHaveBeenCalled();
    expect(push).toHaveBeenCalledWith("/");
  });

  it("shows the server error and stays signed in when deletion is refused", async () => {
    vi.mocked(authClient.deleteUser).mockResolvedValue({
      data: null,
      error: { message: "You have an active subscription. Cancel it from Manage Billing before deleting your account." },
    } as never);
    render(<DeleteAccountSection email="user@example.com" />);
    const user = await openDialog();

    await user.type(screen.getByLabelText(/to confirm/), "user@example.com");
    await user.type(screen.getByLabelText("Password"), "Password123!");
    await user.click(screen.getByRole("button", { name: "Delete account" }));

    expect(await screen.findByText(/Cancel it from Manage Billing/)).toBeInTheDocument();
    expect(authClient.signOut).not.toHaveBeenCalled();
    expect(push).not.toHaveBeenCalled();
  });

  it("warns App Store subscribers to cancel in the App Store", async () => {
    render(<DeleteAccountSection email="user@example.com" hasAppStoreSubscription />);
    await openDialog();

    expect(screen.getByText(/managed through the App Store/)).toBeInTheDocument();
  });
});
