import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import HomeApp from "./home-app";

vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }),
}));

vi.mock("@/lib/auth-client", () => ({
  useSession: () => ({
    data: { user: { id: "user-1", name: "Ada", email: "ada@example.com" } },
    isPending: false,
  }),
  signOut: vi.fn(),
}));

const billingSummary = {
  monetization_enabled: true,
  plan: "pro",
  subscription_status: "active",
  usage_count: 0,
  usage_limit: null,
  remaining: null,
  can_create_task: true,
  upgrade_required: false,
  reason: null,
  max_upload_bytes: 2_000_000_000,
};

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("billing-summary")) return Response.json(billingSummary);
    if (url.includes("/api/tasks")) return Response.json({ tasks: [] });
    if (url.includes("/api/fonts")) return Response.json({ fonts: [] });
    if (url.includes("caption-templates")) return Response.json({ templates: [] });
    if (url.includes("announcements")) return Response.json({ announcements: [] });
    if (url.includes("social")) return Response.json({ enabled: false });
    if (url.includes("service-status")) return Response.json({ status: "operational" });
    return Response.json({});
  }));
});

function videoFile(name: string, size: number) {
  const file = new File(["video"], name, { type: "video/mp4" });
  Object.defineProperty(file, "size", { value: size });
  return file;
}

it("states the upload limit and rejects a file over it before upload", async () => {
  render(<HomeApp />, { wrapper: TooltipProvider });

  expect(await screen.findByText(/Uploads up to 2 GB/)).toBeInTheDocument();
  expect(screen.getByText(/above 1080p or 60 fps are scaled down automatically/)).toBeInTheDocument();

  const input = document.getElementById("video-upload") as HTMLInputElement;
  fireEvent.change(input, { target: { files: [videoFile("talk.mp4", 2_000_000_001)] } });

  expect(await screen.findByText(/This file is 2.0 GB, which is over the 2 GB upload limit/)).toBeInTheDocument();
  expect(screen.queryByText(/ready to upload/)).not.toBeInTheDocument();

  fireEvent.change(input, { target: { files: [videoFile("talk.mp4", 2_000_000_000)] } });

  await waitFor(() => expect(screen.getByText(/ready to upload/)).toBeInTheDocument());
  expect(screen.queryByText(/over the 2 GB upload limit/)).not.toBeInTheDocument();
});
