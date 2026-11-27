import { render, screen } from "@testing-library/react";

import { SaleFeature, SalePill } from "./sale-pill";

function at(iso: string, reducedMotion = false) {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(iso));
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: reducedMotion && query.includes("reduce"),
    addEventListener: () => {},
    removeEventListener: () => {},
  }));
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("Black Friday on the landing page", () => {
  it("shows the pill and the looping card while the sale runs, both linking to pricing", async () => {
    at("2026-11-28T12:00:00Z");
    render(
      <>
        <SalePill />
        <SaleFeature />
      </>,
    );

    expect(await screen.findByTestId("sale-pill")).toHaveAttribute("href", "#pricing");
    const card = await screen.findByTestId("sale-feature");
    const link = card.querySelector("a");
    expect(link).toHaveAttribute("href", "#pricing");
    expect(link).toHaveAccessibleName("Black Friday: 40% off Pro and Creator until Dec 6");
    const sources = [...card.querySelectorAll("video source")].map((s) => s.getAttribute("type"));
    expect(sources).toEqual(["video/webm", "video/mp4"]);
  });

  it("shows the still instead of the loop to visitors who prefer reduced motion", async () => {
    at("2026-11-28T12:00:00Z", true);
    render(<SaleFeature />);

    const card = await screen.findByTestId("sale-feature");
    expect(card.querySelector("video")).toBeNull();
    expect(card.querySelector("img")?.getAttribute("src")).toMatch(/black-friday-studio\.webp$/);
  });

  it("shows nothing once the sale has ended", () => {
    at("2026-12-07T08:00:00Z");
    render(
      <>
        <SalePill />
        <SaleFeature />
      </>,
    );

    expect(screen.queryByTestId("sale-pill")).toBeNull();
    expect(screen.queryByTestId("sale-feature")).toBeNull();
  });
});
