import { render, screen } from "@testing-library/react";

import { PriceTaxNote } from "./upgrade-prompt";

describe("PriceTaxNote", () => {
  const env = process.env;

  beforeEach(() => {
    process.env = { ...env };
    delete process.env.NEXT_PUBLIC_STRIPE_MANAGED_PAYMENTS_ENABLED;
    delete process.env.STRIPE_MANAGED_PAYMENTS_ENABLED;
  });

  afterAll(() => {
    process.env = env;
  });

  it("leaves price copy unchanged when the flag is off", () => {
    const { container } = render(<PriceTaxNote />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows + applicable tax when the public flag is on", () => {
    process.env.NEXT_PUBLIC_STRIPE_MANAGED_PAYMENTS_ENABLED = "true";
    render(<PriceTaxNote />);
    expect(screen.getByText("+ applicable tax")).toBeInTheDocument();
  });
});
