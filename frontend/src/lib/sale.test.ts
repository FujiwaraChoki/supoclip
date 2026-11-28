import { activeSale, BLACK_FRIDAY_2026, saleApplies, salePrice } from "@/lib/sale";

describe("sale", () => {
  it("runs from its start until the end of Sunday 6 December, Pacific time", () => {
    expect(activeSale(new Date("2026-11-26T23:59:59Z"))).toBeNull();
    expect(activeSale(new Date("2026-11-27T00:00:00Z"))).toBe(BLACK_FRIDAY_2026);
    expect(activeSale(new Date("2026-12-07T07:59:59Z"))).toBe(BLACK_FRIDAY_2026);
    expect(activeSale(new Date("2026-12-07T08:00:00Z"))).toBeNull();
  });

  it.each([
    ["9.99", "5.99"],
    ["99.90", "59.94"],
    ["24.99", "14.99"],
    ["249.90", "149.94"],
    ["10", "6"],
    ["19", "11.40"],
  ])("takes 40%% off %s the way Stripe rounds it", (price, expected) => {
    expect(salePrice(price, BLACK_FRIDAY_2026)).toBe(expected);
  });


  it("covers yearly plans only", () => {
    expect(saleApplies(BLACK_FRIDAY_2026, "year")).toBe(true);
    expect(saleApplies(BLACK_FRIDAY_2026, "month")).toBe(false);
  });
});
