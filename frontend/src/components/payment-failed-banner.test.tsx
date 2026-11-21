import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, vi } from "vitest";

import { PaymentFailedBanner } from "./payment-failed-banner";

vi.mock("@/lib/datafast", () => ({ track: vi.fn() }));
vi.mock("sonner", () => ({ toast: { error: vi.fn() } }));

afterEach(() => vi.restoreAllMocks());

function teamsResponse(role: "owner" | "admin" | "member") {
  return Response.json({
    activeTeamId: "team_example_1",
    teams: [{ id: "team_example_1", name: "Northwind Studio", slug: "northwind", ownerId: "user_example_1", role, plan: "team", subscriptionStatus: "past_due", seatLimit: 3, memberCount: 2, pendingInviteCount: 0, occupiedSeats: 2 }],
  });
}

describe("PaymentFailedBanner", () => {
  it("opens the existing billing portal instead of a new checkout", async () => {
    const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      Response.json({ url: "https://billing.stripe.test/session" }),
    );
    render(<PaymentFailedBanner />);

    expect(
      screen.getByText("Your last payment failed. Update your payment method to keep your plan."),
    ).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Update payment method" }));

    expect(fetch).toHaveBeenCalledWith("/api/billing/portal", { method: "POST" });
  });

  it("sends a team owner to team settings and never opens the personal portal", async () => {
    const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(async () => teamsResponse("owner"));
    render(<PaymentFailedBanner teamId="team_example_1" />);

    expect(await screen.findByRole("link", { name: "Update payment method" })).toHaveAttribute("href", "/settings/team");
    expect(screen.getByText(/Your team's last payment failed/)).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
    expect(fetch.mock.calls.some(([input]) => String(input).includes("/api/billing/portal"))).toBe(false);
  });
});
