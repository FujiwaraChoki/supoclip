import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { ANNOUNCEMENTS_UPDATED_EVENT, type ManagedAnnouncement } from "@/lib/announcements";
import { AnnouncementManager } from "./announcement-manager";

const draft: ManagedAnnouncement = {
  id: "a-1",
  title: "Creator program",
  description: "Earn with your audience.",
  href: "/affiliate",
  action: "Learn more",
  publishedAt: "2026-11-01T09:00:00Z",
  isPublished: false,
};

describe("AnnouncementManager", () => {
  beforeEach(() => vi.stubGlobal("fetch", vi.fn()));
  afterEach(() => vi.unstubAllGlobals());

  it("publishes a draft in place and tells the banner to refresh", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(Response.json({ announcements: [draft] }))
      .mockResolvedValueOnce(Response.json({ ok: true }));
    const onUpdated = vi.fn();
    window.addEventListener(ANNOUNCEMENTS_UPDATED_EVENT, onUpdated);
    render(<AnnouncementManager />);

    const row = (await screen.findByText("Creator program")).closest("article")!;
    expect(within(row).getByText("Draft")).toBeInTheDocument();
    fireEvent.click(within(row).getByRole("button", { name: "Publish" }));

    expect(await screen.findByRole("status")).toHaveTextContent("Announcement published.");
    expect(within(row).getByText("Published")).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: "Hide" })).toBeInTheDocument();
    expect(onUpdated).toHaveBeenCalledTimes(1);
    // The list is updated from the answer, not fetched again.
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(vi.mocked(fetch).mock.calls[1][0]).toBe("/api/admin/announcements/a-1");
    window.removeEventListener(ANNOUNCEMENTS_UPDATED_EVENT, onUpdated);
  });

  it("says why a load failed and loads again on retry", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(Response.json({ error: "Database unavailable" }, { status: 500 }))
      .mockResolvedValueOnce(Response.json({ announcements: [draft] }));
    render(<AnnouncementManager />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Database unavailable");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByText("Creator program")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  });

  it("keeps the editor open with the server's reason when saving fails", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(Response.json({ announcements: [] }))
      .mockResolvedValueOnce(Response.json({ error: "Destination must be a path" }, { status: 400 }));
    render(<AnnouncementManager />);

    expect(await screen.findByText(/No announcements yet/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "New announcement" }));
    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Hello" } });
    fireEvent.change(screen.getByLabelText("Description"), { target: { value: "World" } });
    fireEvent.change(screen.getByLabelText("Destination path"), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Destination must be a path");
    expect(screen.getByRole("heading", { name: "New announcement" })).toBeInTheDocument();
  });
});
