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
    vi.mocked(signUp.email).mockResolvedValue({ error: null } as never);
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

  it("completes sign up when DataFast throws a SecurityError", async () => {
    vi.mocked(signUp.email).mockResolvedValue({ error: null } as never);
    vi.stubEnv("NEXT_PUBLIC_DATAFAST_WEBSITE_ID", "site");
    vi.stubEnv("NEXT_PUBLIC_DATAFAST_DOMAIN", "example.com");
    vi.resetModules();
    const { SignUp: FreshSignUp } = await import("./sign-up");
    const tilly = vi.fn();
    window.tilly = tilly as never;
    window.datafast = vi.fn(() => {
      throw new DOMException("localStorage is blocked", "SecurityError");
    }) as never;
    const user = userEvent.setup();

    render(<FreshSignUp />);

    await user.type(screen.getByPlaceholderText("Full Name"), "Jane Admin");
    await user.type(screen.getByPlaceholderText("Email"), "admin@example.com");
    await user.type(screen.getByPlaceholderText("Password"), "Password123!");
    await user.click(screen.getByRole("button", { name: "Sign Up" }));

    expect(
      await screen.findByText("Account created successfully! Signing you in..."),
    ).toBeInTheDocument();
    expect(tilly).toHaveBeenCalledWith("signup_completed", expect.anything());
    expect(screen.getByRole("button", { name: "Sign Up" })).toBeEnabled();

    vi.unstubAllEnvs();
    window.datafast = undefined;
    window.tilly = undefined;
  });

  it("clears loading and shows an error when sign up rejects", async () => {
    vi.mocked(signUp.email).mockRejectedValue(new Error("network down"));
    const user = userEvent.setup();

    render(<SignUp />);

    await user.type(screen.getByPlaceholderText("Full Name"), "Jane Admin");
    await user.type(screen.getByPlaceholderText("Email"), "admin@example.com");
    await user.type(screen.getByPlaceholderText("Password"), "Password123!");
    await user.click(screen.getByRole("button", { name: "Sign Up" }));

    expect(await screen.findByRole("button", { name: "Sign Up" })).toBeEnabled();
    expect(screen.getByText(/failed/i)).toBeInTheDocument();
  });
});
