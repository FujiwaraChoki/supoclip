import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { FeedbackWebhookManager } from "./feedback-webhook-manager";

const webhook = {
  id: "hook-1",
  name: "Team Discord",
  kind: "discord",
  url_hint: "discord.com/…k3Yz",
  categories: ["bug", "feature"],
  enabled: true,
  env_source: null as string | null,
  has_secret: false,
  created_at: "2026-11-17T09:00:00Z",
  updated_at: "2026-11-17T09:00:00Z",
  pending: 0,
  failed: 2,
  last_delivered_at: "2026-11-17T10:00:00Z",
};
const listing = (webhooks = [webhook], environment: unknown[] = []) =>
  Response.json({ webhooks, categories: ["bug", "feature", "general", "sales", "cancellation"], environment });

describe("FeedbackWebhookManager", () => {
  beforeEach(() => vi.stubGlobal("fetch", vi.fn()));
  afterEach(() => vi.unstubAllGlobals());

  it("lists webhooks by hint only, with their categories and failures", async () => {
    vi.mocked(fetch).mockResolvedValue(listing());
    render(<FeedbackWebhookManager />);

    expect(await screen.findByText("Team Discord")).toBeInTheDocument();
    expect(screen.getByText("discord.com/…k3Yz")).toBeInTheDocument();
    expect(screen.getByText("Bug report")).toBeInTheDocument();
    expect(screen.getByText("2 failed deliveries")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Send feedback to Team Discord" })).toHaveAttribute("aria-checked", "true");
  });

  it("shows the server-configured Hermes webhook read-only, with its switch, test and log", async () => {
    const hermes = {
      ...webhook, id: "hook-h", name: "Hermes assistant", kind: "hermes", url_hint: "", env_source: "HERMES_FEEDBACK_WEBHOOK_URL",
      categories: ["bug", "feature", "general", "sales", "cancellation"], failed: 0,
    };
    vi.mocked(fetch)
      .mockResolvedValueOnce(listing([hermes]))
      .mockResolvedValueOnce(Response.json({ delivery: { id: "d1", status: "delivered", status_code: 202, error: null } }));
    render(<FeedbackWebhookManager />);

    const row = (await screen.findByText("Hermes assistant")).closest("li")!;
    expect(within(row).getByText("Configured on the server. Receives every category.")).toBeInTheDocument();
    expect(within(row).queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
    expect(within(row).queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();
    expect(within(row).queryByText(/HERMES_FEEDBACK_WEBHOOK_URL/)).not.toBeInTheDocument();
    expect(within(row).getByRole("switch", { name: "Send feedback to Hermes assistant" })).toBeEnabled();
    expect(within(row).getByRole("button", { name: "Deliveries" })).toBeInTheDocument();

    fireEvent.click(within(row).getByRole("button", { name: "Send test" }));
    await waitFor(() => expect(fetch).toHaveBeenLastCalledWith("/api/admin/feedback-webhooks/hook-h/test", { method: "POST" }));
  });

  it("creates a JSON webhook and shows its secret once", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(listing([]))
      .mockResolvedValueOnce(Response.json({ webhook: { ...webhook, id: "hook-2", name: "Zapier", kind: "json" }, secret: "whsec_abc123" }, { status: 201 }))
      .mockResolvedValueOnce(listing());
    render(<FeedbackWebhookManager />);

    fireEvent.click(await screen.findByRole("button", { name: "Add webhook" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Zapier" } });
    fireEvent.click(screen.getByLabelText(/JSON \(signed\)/));
    fireEvent.change(screen.getByLabelText(/Webhook URL/), { target: { value: " https://hooks.example.com/x " } });
    fireEvent.click(screen.getByLabelText("Cancellation reason"));
    fireEvent.click(screen.getByRole("button", { name: "Save webhook" }));

    expect(await screen.findByText("whsec_abc123")).toBeInTheDocument();
    const [, init] = vi.mocked(fetch).mock.calls[1];
    expect(JSON.parse(init!.body as string)).toEqual({
      name: "Zapier", kind: "json", url: "https://hooks.example.com/x", categories: ["bug", "feature", "general", "sales"], enabled: true,
    });
    expect(screen.getByRole("status")).toHaveTextContent("Webhook added.");
  });

  it("explains a refused URL", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(listing([]))
      .mockResolvedValueOnce(Response.json({ detail: { code: "url_private_address", message: "x" } }, { status: 422 }));
    render(<FeedbackWebhookManager />);

    fireEvent.click(await screen.findByRole("button", { name: "Add webhook" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Internal" } });
    fireEvent.change(screen.getByLabelText(/Webhook URL/), { target: { value: "https://discord.com/api/webhooks/1/x" } });
    fireEvent.click(screen.getByRole("button", { name: "Save webhook" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Webhooks must point to a public internet address");
  });

  it("edits without resending the URL when the field is left empty", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(listing())
      .mockResolvedValueOnce(Response.json({ webhook }))
      .mockResolvedValueOnce(listing());
    render(<FeedbackWebhookManager />);

    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    expect(screen.getByLabelText(/Webhook URL/)).toHaveValue("");
    fireEvent.click(screen.getByRole("button", { name: "Save webhook" }));

    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(3));
    const [url, init] = vi.mocked(fetch).mock.calls[1];
    expect(url).toBe("/api/admin/feedback-webhooks/hook-1");
    expect(JSON.parse(init!.body as string)).toEqual({ name: "Team Discord", categories: ["bug", "feature"], enabled: true });
  });

  it("sends a test and reports the result", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(listing())
      .mockResolvedValueOnce(Response.json({ delivery: { status: "failed", status_code: 404, error: "HTTP 404" } }));
    render(<FeedbackWebhookManager />);

    fireEvent.click(await screen.findByRole("button", { name: "Send test" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Test to Team Discord failed: HTTP 404");
  });

  it("asks before deleting", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(listing())
      .mockResolvedValueOnce(Response.json({ status: "deleted" }))
      .mockResolvedValueOnce(listing([]));
    render(<FeedbackWebhookManager />);

    fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText("Delete Team Discord?")).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete" }));

    expect(await screen.findByText(/No webhooks yet/)).toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls[1]).toEqual(["/api/admin/feedback-webhooks/hook-1", { method: "DELETE" }]);
  });

  it("says when environment webhooks were imported", async () => {
    vi.mocked(fetch).mockResolvedValue(listing([], [
      { env_key: "DISCORD_SALES_WEBHOOK_URL", name: "Sales (Discord)", categories: ["sales"], configured: true, imported_at: "2026-11-17T09:00:00Z" },
    ]));
    render(<FeedbackWebhookManager />);
    expect(await screen.findByText(/Sales \(Discord\) was imported from DISCORD_SALES_WEBHOOK_URL/)).toBeInTheDocument();
  });

  it("keeps no copy of a write-only webhook URL in the query client after saving", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
    vi.mocked(fetch)
      .mockResolvedValueOnce(listing([]))
      .mockResolvedValueOnce(Response.json({ webhook: { ...webhook, id: "hook-2", name: "Ops" } }, { status: 201 }))
      .mockResolvedValueOnce(listing());
    render(<FeedbackWebhookManager />, { wrapper });

    fireEvent.click(await screen.findByRole("button", { name: "Add webhook" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Ops" } });
    fireEvent.change(screen.getByLabelText(/Webhook URL/), { target: { value: "https://discord.com/api/webhooks/1/secret-token" } });
    fireEvent.click(screen.getByRole("button", { name: "Save webhook" }));

    expect(await screen.findByText("Webhook added.")).toBeInTheDocument();
    expect(client.getMutationCache().getAll()).toHaveLength(0);
    const cached = JSON.stringify(client.getQueryCache().getAll().map((query) => query.state.data));
    expect(cached).not.toContain("secret-token");
  });
});
