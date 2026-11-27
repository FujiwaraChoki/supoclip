import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { CreatorOfferBanner } from "./creator-offer-banner";
import { SaleBand, SaleNote, SalePill } from "./sale";

vi.mock("@/lib/creator-code", () => ({
  captureCreatorCodeFromUrl: vi.fn().mockResolvedValue("MAYA"),
}));

function at(iso: string, reducedMotion = false) {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(iso));
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: reducedMotion && query.includes("reduce"),
    addEventListener: () => {},
    removeEventListener: () => {},
  }));
}

const DURING = "2026-11-28T12:00:00Z";
const AFTER = "2026-12-07T08:00:00Z";

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("Black Friday on the landing page", () => {
  it("shows the pill, the band and the corner note while the sale runs, all pointing at pricing", async () => {
    at(DURING);
    render(
      <>
        <SalePill />
        <SaleBand />
        <SaleNote />
      </>,
    );

    expect(await screen.findByTestId("sale-pill")).toHaveAttribute("href", "#pricing");
    const band = await screen.findByTestId("sale-band");
    const link = band.querySelector("a");
    expect(link).toHaveAttribute("href", "#pricing");
    expect(link).toHaveAccessibleName("Black Friday: 40% off Pro and Creator until Dec 6");
    expect([...band.querySelectorAll("video source")].map((s) => s.getAttribute("src"))).toEqual([
      expect.stringMatching(/black-friday-studio\.webm$/),
      expect.stringMatching(/black-friday-studio\.mp4$/),
    ]);
    const note = await screen.findByTestId("sale-note");
    expect(note).toHaveTextContent("40% off Pro and Creator");
    expect(note).toHaveTextContent("Black Friday, until Dec 6. See pricing");
    expect(note.querySelector("video source")?.getAttribute("src")).toMatch(/black-friday-scene\.webm$/);
  });

  it("keeps the corner note closed once dismissed", async () => {
    at(DURING);
    const { unmount } = render(<SaleNote />);
    await userEvent.click(await screen.findByRole("button", { name: "Dismiss" }));
    expect(screen.queryByTestId("sale-note")).toBeNull();
    unmount();

    render(<SaleNote />);
    await Promise.resolve();
    expect(screen.queryByTestId("sale-note")).toBeNull();
  });

  it("shows stills instead of loops to visitors who prefer reduced motion", async () => {
    at(DURING, true);
    render(
      <>
        <SaleBand />
        <SaleNote />
      </>,
    );

    const band = await screen.findByTestId("sale-band");
    expect(band.querySelector("video")).toBeNull();
    expect(band.querySelector("img")?.getAttribute("src")).toMatch(/black-friday-studio\.webp$/);
    const note = await screen.findByTestId("sale-note");
    expect(note.querySelector("img")?.getAttribute("src")).toMatch(/black-friday-scene\.webp$/);
  });

  it("shows nothing once the sale has ended", () => {
    at(AFTER);
    render(
      <>
        <SalePill />
        <SaleBand />
        <SaleNote />
      </>,
    );

    expect(screen.queryByTestId("sale-pill")).toBeNull();
    expect(screen.queryByTestId("sale-band")).toBeNull();
    expect(screen.queryByTestId("sale-note")).toBeNull();
  });

  it("hides the creator-code note during the sale, which checkout applies instead", async () => {
    at(DURING);
    render(<CreatorOfferBanner />);
    await Promise.resolve();
    await Promise.resolve();
    expect(screen.queryByText("MAYA")).toBeNull();
  });

  it("shows the creator-code note again after the sale", async () => {
    at(AFTER);
    render(<CreatorOfferBanner />);
    expect(await screen.findByText("MAYA")).toBeInTheDocument();
  });
});
