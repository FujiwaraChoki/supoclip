import { cleanup, render, screen } from "@testing-library/react";
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

it("shows a compact operational state after recovery", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
    component: { status: "operational", headline: "Resolved", description: "Downloads are working normally." },
  }), { status: 200 })));

  render(<VideoDownloadStatus />);

  expect(await screen.findByText("YouTube imports · Operational")).toBeInTheDocument();
  expect(screen.queryByText("Resolved")).not.toBeInTheDocument();
});

it("never claims service is healthy when the status request fails", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("", { status: 503 })));

  render(<VideoDownloadStatus />);

  expect(await screen.findByText("YouTube import status is temporarily unavailable.")).toBeInTheDocument();
  expect(screen.queryByText("YouTube imports · Operational")).not.toBeInTheDocument();
});
