import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { TooltipProvider } from "@/components/ui/tooltip";
import SocialSettingsPage from "./page";

const replace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/lib/auth-client", () => ({
  useSession: () => ({ data: { user: { id: "user-1", email: "ada@example.com" } }, isPending: false }),
}));
vi.mock("@/components/scheduled-posts", () => ({ ScheduledPosts: () => null }));

const youtube = {
  provider: "youtube",
  display_name: "YouTube",
  configured: true,
  supported_privacy_levels: ["private"],
  setup_hint: null,
};
const connection = {
  id: "conn-1",
  provider: "youtube",
  external_account_id: "yt-1",
  display_name: "Ada Clips",
  username: null,
  avatar_url: null,
  token_expires_at: null,
  status: "active",
  last_error: null,
  created_at: null,
};

describe("SocialSettingsPage", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("disconnects an account and shows the reloaded list", async () => {
    let connections = [connection];
    const request = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === "DELETE") {
        connections = [];
        return Response.json({ ok: true });
      }
      if (url === "/api/social/providers") return Response.json({ enabled: true, providers: [youtube] });
      if (url === "/api/social/connections") return Response.json({ connections });
      return Response.json({});
    });
    vi.stubGlobal("fetch", request);
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<SocialSettingsPage />, { wrapper: TooltipProvider });

    expect(await screen.findByText("Ada Clips")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Disconnect" }));

    await waitFor(() => expect(screen.queryByText("Ada Clips")).not.toBeInTheDocument());
    expect(request).toHaveBeenCalledWith("/api/social/connections/conn-1", { method: "DELETE" });
    expect(request.mock.calls.filter(([url]) => url === "/api/social/connections")).toHaveLength(2);
    confirm.mockRestore();
  });

  it("keeps the account and says why when disconnecting fails", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === "DELETE") return Response.json({ detail: "Token revoke failed" }, { status: 502 });
      if (url === "/api/social/providers") return Response.json({ enabled: true, providers: [youtube] });
      return Response.json({ connections: [connection] });
    }));
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<SocialSettingsPage />, { wrapper: TooltipProvider });

    fireEvent.click(await screen.findByRole("button", { name: "Disconnect" }));
    expect(await screen.findByText("Token revoke failed")).toBeInTheDocument();
    expect(screen.getByText("Ada Clips")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Disconnect" })).toBeEnabled();
    confirm.mockRestore();
  });

  it("explains a server without social publishing", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) =>
      String(input) === "/api/social/providers"
        ? Response.json({ enabled: false, providers: [] })
        : Response.json({ connections: [] })));

    render(<SocialSettingsPage />, { wrapper: TooltipProvider });

    expect(await screen.findByText(/isn't available on your account yet/)).toBeInTheDocument();
  });
});
