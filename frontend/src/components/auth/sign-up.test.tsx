import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import { SignUp } from "./sign-up";
import { signUp } from "../../lib/auth-client";

vi.mock("../../lib/auth-client", () => ({
  signUp: {
    email: vi.fn(),
  },
}));

describe("SignUp", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders API errors", async () => {
    vi.mocked(signUp.email).mockResolvedValue({
      error: { message: "Account already exists" },
    } as never);
    const user = userEvent.setup();

    render(<SignUp />);

    await user.type(screen.getByPlaceholderText("Full Name"), "Jane Admin");
    await user.type(screen.getByPlaceholderText("Email"), "admin@example.com");
    await user.type(screen.getByPlaceholderText("Password"), "Password123!");
    await user.click(screen.getByRole("button", { name: "Sign Up" }));

    expect(await screen.findByText("Account already exists")).toBeInTheDocument();
  });

  it("shows a success state after sign up", async () => {
    vi.mocked(signUp.email).mockResolvedValue({ data: { token: "session-token" }, error: null } as never);
    const user = userEvent.setup();

    render(<SignUp />);

    await user.type(screen.getByPlaceholderText("Full Name"), "Jane Admin");
    await user.type(screen.getByPlaceholderText("Email"), "admin@example.com");
    await user.type(screen.getByPlaceholderText("Password"), "Password123!");
    await user.click(screen.getByRole("button", { name: "Sign Up" }));

    expect(
      await screen.findByText("Account created successfully! Signing you in..."),
    ).toBeInTheDocument();
  });

  it("asks a new account to verify its email when sign-up returns no session", async () => {
    window.history.replaceState(null, "", "/sign-up?next=/invite/abc");
    vi.mocked(signUp.email).mockResolvedValue({ data: { token: null }, error: null } as never);
    const user = userEvent.setup();

    render(<SignUp />);

    await user.type(screen.getByPlaceholderText("Full Name"), "Jane Admin");
    await user.type(screen.getByPlaceholderText("Email"), "admin@example.com");
    await user.type(screen.getByPlaceholderText("Password"), "Password123!");
    await user.click(screen.getByRole("button", { name: "Sign Up" }));

    expect(await screen.findByText("Check your inbox")).toBeInTheDocument();
    expect(screen.getByText(/We sent a link to admin@example.com/)).toBeInTheDocument();
    expect(signUp.email).toHaveBeenCalledWith(expect.objectContaining({ callbackURL: "/invite/abc" }));
    window.history.replaceState(null, "", "/");
  });

  it("explains a server failure when the response has no message", async () => {
    vi.mocked(signUp.email).mockResolvedValue({
      error: { status: 500, statusText: "Internal Server Error", message: "" },
    } as never);
    const user = userEvent.setup();

    render(<SignUp />);

    await user.type(screen.getByPlaceholderText("Full Name"), "Jane Admin");
    await user.type(screen.getByPlaceholderText("Email"), "admin@example.com");
    await user.type(screen.getByPlaceholderText("Password"), "Password123!");
    await user.click(screen.getByRole("button", { name: "Sign Up" }));

    expect(await screen.findByText(/frontend log/)).toBeInTheDocument();
  });

  it("keeps the short message for other errors without a message", async () => {
    vi.mocked(signUp.email).mockResolvedValue({
      error: { status: 400, statusText: "Bad Request", message: "" },
    } as never);
    const user = userEvent.setup();

    render(<SignUp />);

    await user.type(screen.getByPlaceholderText("Full Name"), "Jane Admin");
    await user.type(screen.getByPlaceholderText("Email"), "admin@example.com");
    await user.type(screen.getByPlaceholderText("Password"), "Password123!");
    await user.click(screen.getByRole("button", { name: "Sign Up" }));

    expect(await screen.findByText("Failed to create account")).toBeInTheDocument();
  });
});
