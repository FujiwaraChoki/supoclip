import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import { ForgotPassword } from "./forgot-password";
import { ResetPassword } from "./reset-password";
import { requestPasswordReset, resetPassword } from "../../lib/auth-client";

const searchParams = new URLSearchParams();
const push = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
  useSearchParams: () => searchParams,
}));

vi.mock("../../lib/auth-client", () => ({
  requestPasswordReset: vi.fn(),
  resetPassword: vi.fn(),
}));

describe("ForgotPassword", () => {
  beforeEach(() => vi.clearAllMocks());

  it("requests a reset link that returns to /reset-password", async () => {
    vi.mocked(requestPasswordReset).mockResolvedValue({ error: null } as never);
    const user = userEvent.setup();

    render(<ForgotPassword />);
    await user.type(screen.getByPlaceholderText("Email"), "user@example.com");
    await user.click(screen.getByRole("button", { name: "Send reset link" }));

    expect(requestPasswordReset).toHaveBeenCalledWith({
      email: "user@example.com",
      redirectTo: "/reset-password",
    });
    expect(await screen.findByText(/a reset link is on its way/)).toBeInTheDocument();
  });

  it("shows the generic error and allows a retry when the request rejects", async () => {
    vi.mocked(requestPasswordReset)
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce({ error: null } as never);
    const user = userEvent.setup();

    render(<ForgotPassword />);
    await user.type(screen.getByPlaceholderText("Email"), "user@example.com");
    await user.click(screen.getByRole("button", { name: "Send reset link" }));

    expect(await screen.findByText("Could not send reset link")).toBeInTheDocument();
    expect(screen.queryByText(/Failed to fetch/)).not.toBeInTheDocument();
    expect(screen.getByPlaceholderText("Email")).toBeEnabled();
    const retry = screen.getByRole("button", { name: "Send reset link" });
    expect(retry).toBeEnabled();

    await user.click(retry);
    expect(await screen.findByText(/a reset link is on its way/)).toBeInTheDocument();
    expect(screen.queryByText("Could not send reset link")).not.toBeInTheDocument();
  });

  it("still shows the server's error for an HTTP error response and re-enables the form", async () => {
    vi.mocked(requestPasswordReset).mockResolvedValue({
      error: { message: "Service unavailable" },
    } as never);
    const user = userEvent.setup();

    render(<ForgotPassword />);
    await user.type(screen.getByPlaceholderText("Email"), "user@example.com");
    await user.click(screen.getByRole("button", { name: "Send reset link" }));

    expect(await screen.findByText("Service unavailable")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send reset link" })).toBeEnabled();
  });
});

describe("ResetPassword", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    for (const key of [...searchParams.keys()]) searchParams.delete(key);
  });

  it("shows an expired state without a token", () => {
    render(<ResetPassword />);
    expect(screen.getByText("Link expired")).toBeInTheDocument();
  });

  it("shows an expired state when Better Auth reports an invalid token", () => {
    searchParams.set("error", "INVALID_TOKEN");
    render(<ResetPassword />);
    expect(screen.getByRole("link", { name: "Request a new link" })).toBeInTheDocument();
  });

  it("rejects mismatched passwords", async () => {
    searchParams.set("token", "tok");
    const user = userEvent.setup();

    render(<ResetPassword />);
    await user.type(screen.getByPlaceholderText("New password"), "Password123!");
    await user.type(screen.getByPlaceholderText("Confirm new password"), "Password456!");
    await user.click(screen.getByRole("button", { name: "Reset password" }));

    expect(await screen.findByText("Passwords do not match")).toBeInTheDocument();
    expect(resetPassword).not.toHaveBeenCalled();
  });

  it("resets the password with the token from the link", async () => {
    searchParams.set("token", "tok");
    vi.mocked(resetPassword).mockResolvedValue({ error: null } as never);
    const user = userEvent.setup();

    render(<ResetPassword />);
    await user.type(screen.getByPlaceholderText("New password"), "Password123!");
    await user.type(screen.getByPlaceholderText("Confirm new password"), "Password123!");
    await user.click(screen.getByRole("button", { name: "Reset password" }));

    expect(resetPassword).toHaveBeenCalledWith({ newPassword: "Password123!", token: "tok" });
    expect(await screen.findByText(/Password updated/)).toBeInTheDocument();
  });

  it("shows the generic error and allows a retry when the request rejects", async () => {
    searchParams.set("token", "tok");
    vi.mocked(resetPassword)
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce({ error: null } as never);
    const user = userEvent.setup();

    render(<ResetPassword />);
    await user.type(screen.getByPlaceholderText("New password"), "Password123!");
    await user.type(screen.getByPlaceholderText("Confirm new password"), "Password123!");
    await user.click(screen.getByRole("button", { name: "Reset password" }));

    expect(await screen.findByText("Failed to reset password")).toBeInTheDocument();
    expect(screen.queryByText(/Failed to fetch/)).not.toBeInTheDocument();
    expect(screen.getByPlaceholderText("New password")).toBeEnabled();
    const retry = screen.getByRole("button", { name: "Reset password" });
    expect(retry).toBeEnabled();

    await user.click(retry);
    expect(await screen.findByText(/Password updated/)).toBeInTheDocument();
  });

  it("shows the server's error for an HTTP error response and re-enables the form", async () => {
    searchParams.set("token", "tok");
    vi.mocked(resetPassword).mockResolvedValue({
      error: { message: "Invalid token" },
    } as never);
    const user = userEvent.setup();

    render(<ResetPassword />);
    await user.type(screen.getByPlaceholderText("New password"), "Password123!");
    await user.type(screen.getByPlaceholderText("Confirm new password"), "Password123!");
    await user.click(screen.getByRole("button", { name: "Reset password" }));

    expect(await screen.findByText("Invalid token")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reset password" })).toBeEnabled();
  });
});
