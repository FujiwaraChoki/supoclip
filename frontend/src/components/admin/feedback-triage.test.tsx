import { fireEvent, render, screen } from "@testing-library/react";
import { FeedbackTriage } from "./feedback-triage";
import type { FeedbackSubmission } from "@/lib/feedback-inbox";

const refresh = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh }) }));

const submission = {
  id: "sub-1",
  status: "open",
  resolved_at: null,
  resolved_by_name: null,
  notes: [{ id: "n1", body: "Asked for a screen recording", created_at: "2026-11-17T09:00:00Z", author_id: "a", author_name: "Sami" }],
  deliveries: [
    { id: "d1", webhook_id: "w1", webhook_name: "Team Discord", webhook_kind: "discord", status: "failed", attempts: 8,
      next_attempt_at: null, last_status_code: 500, last_error: "HTTP 500", delivered_at: null, created_at: "2026-11-17T09:00:00Z" },
  ],
} as unknown as FeedbackSubmission;

describe("FeedbackTriage", () => {
  beforeEach(() => {
    refresh.mockReset();
    vi.stubGlobal("fetch", vi.fn());
  });
  afterEach(() => vi.unstubAllGlobals());

  it("changes the status", async () => {
    vi.mocked(fetch).mockResolvedValue(Response.json({ submission: { ...submission, status: "resolved", resolved_at: "2026-11-17T10:00:00Z" } }));
    render(<FeedbackTriage submission={submission} />);

    expect(screen.getByRole("button", { name: "Open" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "Resolved" }));

    expect(await screen.findByText("Marked as Resolved.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Resolved" })).toHaveAttribute("aria-pressed", "true");
    expect(fetch).toHaveBeenCalledWith("/api/admin/feedback/sub-1", expect.objectContaining({ method: "PATCH", body: JSON.stringify({ status: "resolved" }) }));
    expect(refresh).toHaveBeenCalled();
  });

  it("adds a note", async () => {
    vi.mocked(fetch).mockResolvedValue(Response.json({ note: { id: "n2", body: "Fixed in 2.5", created_at: "2026-11-17T11:00:00Z", author_id: "a" } }, { status: 201 }));
    render(<FeedbackTriage submission={submission} />);

    expect(screen.getByText("Asked for a screen recording")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Add a note"), { target: { value: "Fixed in 2.5" } });
    fireEvent.click(screen.getByRole("button", { name: "Add note" }));

    expect(await screen.findByText("Fixed in 2.5")).toBeInTheDocument();
    expect(screen.getByLabelText("Add a note")).toHaveValue("");
  });

  it("retries a failed delivery", async () => {
    vi.mocked(fetch).mockResolvedValue(Response.json({ status: "pending" }));
    render(<FeedbackTriage submission={submission} />);

    fireEvent.click(screen.getByRole("button", { name: "Retry delivery to Team Discord" }));
    expect(await screen.findByText("Delivery queued again.")).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith("/api/admin/feedback/deliveries/d1/retry", { method: "POST" });
  });

  it("keeps the status and says why when saving fails", async () => {
    vi.mocked(fetch).mockResolvedValue(Response.json({ detail: "Admin access required" }, { status: 403 }));
    render(<FeedbackTriage submission={submission} />);

    fireEvent.click(screen.getByRole("button", { name: "In progress" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Admin access required");
    expect(screen.getByRole("button", { name: "Open" })).toHaveAttribute("aria-pressed", "true");
  });
});
