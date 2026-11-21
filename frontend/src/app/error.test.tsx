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
    window.sessionStorage.clear();
    vi.restoreAllMocks();
  });

  function chunkError() {
    const error = new Error("Loading chunk 123 failed.");
    error.name = "ChunkLoadError";
    return error;
  }

  it("reloads by itself once when a chunk failed to load", () => {
    stubReload();
    const { unmount } = render(<ErrorPage error={chunkError()} />);
    expect(reload).toHaveBeenCalledTimes(1);
    unmount();

    // The reloaded page hit the same failure: stay on the error screen instead of looping.
    render(<ErrorPage error={chunkError()} />);
    expect(reload).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();
  });

  it("reloads by itself once from the global error page when a chunk failed to load", () => {
    stubReload();
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(<GlobalError error={chunkError()} />);
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("does not reload by itself for other errors", () => {
    stubReload();
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(<ErrorPage error={new Error("boom")} />);
    render(<GlobalError error={new Error("boom")} />);
    expect(reload).not.toHaveBeenCalled();
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
