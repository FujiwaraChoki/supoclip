import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { VideoDownloadStatus } from "./video-download-status";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("shows an active incident and links to the full status page", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
    component: {
      status: "outage", headline: "Investigating YouTube import failures",
      description: "New imports may fail before processing starts.",
    },
  }), { status: 200 })));

  render(<VideoDownloadStatus />);

  expect(await screen.findByText("YouTube imports · Outage")).toBeInTheDocument();
  expect(screen.getByText("New imports may fail before processing starts.")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "View status page" })).toHaveAttribute("href", "/status");
});

it("hides the banner when service is operational", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
    component: { status: "operational", headline: "Resolved", description: "Downloads are working normally." },
  }), { status: 200 })));

  const { container } = render(<VideoDownloadStatus />);

  await act(async () => {});
  expect(container).toBeEmptyDOMElement();
});

it("never claims service is healthy when the status request fails", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("", { status: 503 })));

  const { container } = render(<VideoDownloadStatus />);

  await act(async () => {});
  expect(container).toBeEmptyDOMElement();
});

it("removes an incident banner when a refresh reports recovery", async () => {
  const fetch = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ component: {
      status: "degraded", headline: "Investigating", description: "Downloads are slow.",
    } })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ component: {
      status: "operational", headline: "Resolved", description: "Downloads are working normally.",
    } })));
  vi.stubGlobal("fetch", fetch);
  const { container } = render(<VideoDownloadStatus />);
  expect(await screen.findByText("YouTube imports · Degraded")).toBeInTheDocument();

  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  act(() => document.dispatchEvent(new Event("visibilitychange")));

  await waitFor(() => expect(container).toBeEmptyDOMElement());
  expect(fetch).toHaveBeenCalledTimes(2);
});
