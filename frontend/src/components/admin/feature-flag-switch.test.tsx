import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { FeatureFlagSwitch } from "./feature-flag-switch";

const refresh = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh }) }));

function renderSwitch(checked = false) {
  return render(<FeatureFlagSwitch flagKey="workflows" field="web_enabled" checked={checked} label="workflows on Web" />);
}

describe("FeatureFlagSwitch", () => {
  beforeEach(() => {
    refresh.mockReset();
    vi.stubGlobal("fetch", vi.fn());
  });
  afterEach(() => vi.unstubAllGlobals());

  it("turns a flag on for one platform and refreshes the page", async () => {
    vi.mocked(fetch).mockResolvedValue(Response.json({ flag: { key: "workflows", web_enabled: true } }));
    renderSwitch(false);

    const toggle = screen.getByRole("switch", { name: "workflows on Web" });
    expect(toggle).toHaveAttribute("aria-checked", "false");
    fireEvent.click(toggle);

    await waitFor(() => expect(refresh).toHaveBeenCalled());
    expect(fetch).toHaveBeenCalledWith("/api/admin/feature-flags/workflows", expect.objectContaining({
      method: "PATCH",
      body: JSON.stringify({ web_enabled: true }),
    }));
    expect(toggle).toHaveAttribute("aria-checked", "true");
  });

  it("puts the switch back and says why when saving fails", async () => {
    vi.mocked(fetch).mockResolvedValue(Response.json({ error: "Forbidden" }, { status: 403 }));
    renderSwitch(true);

    const toggle = screen.getByRole("switch", { name: "workflows on Web" });
    fireEvent.click(toggle);

    expect(await screen.findByText("Forbidden")).toBeInTheDocument();
    expect(toggle).toHaveAttribute("aria-checked", "true");
    expect(refresh).not.toHaveBeenCalled();
  });
});
