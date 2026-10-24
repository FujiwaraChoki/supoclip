import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import { PaymentFailedBanner } from "./payment-failed-banner";

vi.mock("@/lib/datafast", () => ({ track: vi.fn() }));
vi.mock("sonner", () => ({ toast: { error: vi.fn() } }));

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
});
