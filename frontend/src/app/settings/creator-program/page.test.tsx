import { render, screen } from "@testing-library/react";
import CreatorProgramPage from "./page";

vi.mock("@/lib/auth-client", () => ({
  useSession: () => ({ data: { user: { id: "user-1", email: "ada@example.com" } }, isPending: false }),
}));

describe("CreatorProgramPage", () => {
  afterEach(() => vi.unstubAllGlobals());

  const earnings = {
    terms: { percent: 30, months: 12, hold_days: 30, minimum_payout: 2500 },
    totals: [{ currency: "usd", pending: 840, available: 780, in_transit: 0, paid: 1200 }],
    referrals: { total: 3, customers: 3 },
    activity: [
      { occurred_at: "2026-11-19T10:00:00Z", kind: "refund", amount: -300, currency: "usd", available_at: "2026-11-19T10:00:00Z", payout_status: null },
    ],
    payout_history: [],
    payouts: { status: "not_started", country: null, details_submitted: false },
  };

  function approvedFetch() {
    return vi.fn(async (url: string) =>
      url === "/api/affiliates/earnings"
        ? Response.json(earnings)
        : Response.json({
            application: { slug: "ada", status: "approved", decline_reason: null, app_store_code_added_at: null, country: "US" },
            can_apply_at: null,
          }),
    );
  }

  it("shows an approved creator's code", async () => {
    vi.stubGlobal("fetch", approvedFetch());

    render(<CreatorProgramPage />);

    expect(await screen.findByText("You're in")).toBeInTheDocument();
    expect(screen.getByText("ADA")).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith("/api/affiliates", { cache: "no-store" });
  });

  it("shows an approved creator's earnings and payout setup", async () => {
    vi.stubGlobal("fetch", approvedFetch());

    render(<CreatorProgramPage />);

    expect(await screen.findByText("Your earnings")).toBeInTheDocument();
    expect(screen.getByText("$8.40")).toBeInTheDocument();
    expect(screen.getByText("$7.80")).toBeInTheDocument();
    expect(screen.getByText("$12.00")).toBeInTheDocument();
    expect(screen.getByText("-$3.00")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Set up payouts" })).toBeInTheDocument();
  });

  it("asks creators who applied before the form asked where they live", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({
      application: { slug: "ada", status: "approved", decline_reason: null, app_store_code_added_at: null, country: null },
      can_apply_at: null,
    })));

    render(<CreatorProgramPage />);

    expect(await screen.findByText("Where do you live?")).toBeInTheDocument();
    expect(screen.queryByText("Your earnings")).not.toBeInTheDocument();
  });

  it("tells creators outside the payout countries that they keep Pro but earn no commission", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({
      application: { slug: "ada", status: "approved", decline_reason: null, app_store_code_added_at: null, country: "ZZ" },
      can_apply_at: null,
    })));

    render(<CreatorProgramPage />);

    expect(await screen.findByText("Commission isn't available where you live")).toBeInTheDocument();
    expect(fetch).not.toHaveBeenCalledWith("/api/affiliates/earnings", expect.anything());
  });

  it("says when the application could not be loaded", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ error: "boom" }, { status: 500 })));

    render(<CreatorProgramPage />);

    expect(await screen.findByText("Failed to load your application")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Apply" })).not.toBeInTheDocument();
  });
});
