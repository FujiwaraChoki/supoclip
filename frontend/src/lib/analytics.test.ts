import posthog from "posthog-js";

const config = vi.hoisted(() => ({ isPostHogEnabled: true }));

vi.mock("posthog-js", () => ({ default: { capture: vi.fn() } }));
vi.mock("@/lib/posthog-config", () => config);

import { trackEvent } from "./analytics";

describe("trackEvent", () => {
  beforeEach(() => {
    vi.mocked(posthog.capture).mockReset();
    config.isPostHogEnabled = true;
  });

  it("sends the event and its properties to PostHog", () => {
    trackEvent("generate_clicked", {
      workflow: "highlights",
      source_kind: "youtube",
      has_instructions: false,
      blocked_by_upgrade: true,
    });

    expect(posthog.capture).toHaveBeenCalledWith("generate_clicked", {
      workflow: "highlights",
      source_kind: "youtube",
      has_instructions: false,
      blocked_by_upgrade: true,
    });
  });

  it("does nothing without a PostHog key", () => {
    config.isPostHogEnabled = false;
    trackEvent("source_entered", { source_kind: "upload" });
    expect(posthog.capture).not.toHaveBeenCalled();
  });

  it("never throws when PostHog fails", () => {
    vi.mocked(posthog.capture).mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(() => trackEvent("editor_opened", { clip_count: 3 })).not.toThrow();
  });
});
