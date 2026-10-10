import { applyAvailableAt, grantAffiliatePro, parseApplication, validateSlug } from "./affiliates";

describe("validateSlug", () => {
  it.each(["maya", "Maya", "  maya-clips ", "abc", "a1b2c3d4e5f6g7h8i9j0"])("accepts %j", (slug) => {
    expect(validateSlug(slug)).toEqual({ ok: true, slug: slug.trim().toLowerCase() });
  });

  it.each([
    ["too short", "ab"],
    ["too long", "a".repeat(21)],
    ["leading hyphen", "-maya"],
    ["trailing hyphen", "maya-"],
    ["double hyphen", "ma--ya"],
    ["spaces", "ma ya"],
    ["symbols", "maya!"],
    ["reserved", "admin"],
    ["brand prefix", "supoclip-deals"],
    ["not a string", 42],
  ])("rejects %s", (_label, slug) => {
    expect(validateSlug(slug).ok).toBe(false);
  });
});

describe("parseApplication", () => {
  const valid = {
    slug: "Maya",
    platform: "tiktok",
    profile_url: "https://www.tiktok.com/@maya",
    audience_size: "1k-10k",
    video_url: "",
    promotion_plan: "  Weekly editing tips  ",
    accept_terms: true,
  };

  it("normalizes a valid application", () => {
    expect(parseApplication(valid)).toEqual({
      ok: true,
      application: {
        slug: "maya",
        platform: "tiktok",
        profile_url: "https://www.tiktok.com/@maya",
        audience_size: "1k-10k",
        video_url: null,
        promotion_plan: "Weekly editing tips",
      },
    });
  });

  it.each([
    ["unknown platform", { platform: "myspace" }],
    ["unknown audience size", { audience_size: "huge" }],
    ["missing profile", { profile_url: "" }],
    ["javascript profile link", { profile_url: "javascript:alert(1)" }],
    ["relative video link", { video_url: "/watch" }],
    ["terms not accepted", { accept_terms: "yes" }],
  ])("rejects %s", (_label, override) => {
    expect(parseApplication({ ...valid, ...override }).ok).toBe(false);
  });
});

describe("applyAvailableAt", () => {
  const now = new Date("2026-10-09T12:00:00Z");

  it("lets new applicants apply now", () => {
    expect(applyAvailableAt(null, now)).toEqual(now);
  });

  it.each(["pending", "approved", "revoked"])("blocks reapplying while %s", (status) => {
    expect(applyAvailableAt({ status, reviewed_at: now }, now)).toBeNull();
  });

  it("allows reapplying 30 days after a decline", () => {
    const declined = { status: "declined", reviewed_at: new Date("2026-10-01T12:00:00Z") };
    expect(applyAvailableAt(declined, now)).toEqual(new Date("2026-10-31T12:00:00Z"));
    expect(applyAvailableAt(declined, new Date("2026-11-05T00:00:00Z"))).toEqual(new Date("2026-11-05T00:00:00Z"));
  });
});

describe("grantAffiliatePro", () => {
  it("never overrides an active paid subscription", async () => {
    const updateMany = vi.fn().mockResolvedValue({ count: 1 });

    await grantAffiliatePro({ user: { updateMany } } as never, "user-1");

    expect(updateMany).toHaveBeenCalledWith({
      where: {
        id: "user-1",
        OR: [{ subscription_provider: "affiliate" }, { subscription_status: { notIn: ["active", "trialing"] } }],
      },
      data: { plan: "pro", subscription_status: "active", subscription_provider: "affiliate" },
    });
  });
});
