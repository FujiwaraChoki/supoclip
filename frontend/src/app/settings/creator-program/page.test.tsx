import { render, screen } from "@testing-library/react";
import CreatorProgramPage from "./page";

vi.mock("@/lib/auth-client", () => ({
  useSession: () => ({ data: { user: { id: "user-1", email: "ada@example.com" } }, isPending: false }),
}));

describe("CreatorProgramPage", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows an approved creator's code", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({
      application: { slug: "ada", status: "approved", decline_reason: null, app_store_code_added_at: null },
      can_apply_at: null,
    })));

    render(<CreatorProgramPage />);

    expect(await screen.findByText("You're in")).toBeInTheDocument();
    expect(screen.getByText("ADA")).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith("/api/affiliates", { cache: "no-store" });
  });

  it("says when the application could not be loaded", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ error: "boom" }, { status: 500 })));

    render(<CreatorProgramPage />);

    expect(await screen.findByText("Failed to load your application")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Apply" })).not.toBeInTheDocument();
  });
});
