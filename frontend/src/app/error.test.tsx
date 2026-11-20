import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import ErrorPage from "./error";
import GlobalError from "./global-error";

vi.mock("posthog-js", () => ({ default: { captureException: vi.fn() } }));
vi.mock("./globals.css", () => ({}));

describe("error boundaries", () => {
  const original = window.location;
  const reload = vi.fn();

  function stubReload() {
    Object.defineProperty(window, "location", { configurable: true, value: { ...original, reload } });
  }

  afterEach(() => {
    Object.defineProperty(window, "location", { configurable: true, value: original });
    reload.mockReset();
    vi.restoreAllMocks();
  });

  it("reloads the page on Try again instead of re-rendering over a broken DOM", async () => {
    stubReload();
    const reset = vi.fn();
    render(<ErrorPage error={new Error("boom")} {...({ reset } as object)} />);

    await userEvent.setup().click(screen.getByRole("button", { name: "Try again" }));

    expect(reload).toHaveBeenCalledTimes(1);
    expect(reset).not.toHaveBeenCalled();
  });

  it("reloads from the global error page too", async () => {
    stubReload();
    const reset = vi.fn();
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(<GlobalError error={new Error("boom")} {...({ reset } as object)} />);

    await userEvent.setup().click(screen.getByRole("button", { name: "Try again" }));

    expect(reload).toHaveBeenCalledTimes(1);
    expect(reset).not.toHaveBeenCalled();
  });
});
