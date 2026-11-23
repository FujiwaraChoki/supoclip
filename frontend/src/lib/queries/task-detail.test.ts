import { describe, expect, it } from "vitest";
import type { SocialPost } from "@/lib/social";
import { socialPostsPollInterval, SOCIAL_POSTS_IN_FLIGHT_POLL_MS, SOCIAL_POSTS_SCHEDULED_POLL_MS } from "./task-detail";

const post = (status: string) => ({ id: status, status }) as unknown as SocialPost;

describe("socialPostsPollInterval", () => {
  it("polls fast while an upload is in flight, slowly while one is scheduled, and not at all otherwise", () => {
    expect(socialPostsPollInterval([post("scheduled"), post("publishing")])).toBe(SOCIAL_POSTS_IN_FLIGHT_POLL_MS);
    expect(socialPostsPollInterval([post("published"), post("scheduled")])).toBe(SOCIAL_POSTS_SCHEDULED_POLL_MS);
    expect(socialPostsPollInterval([post("published"), post("failed")])).toBe(false);
    expect(socialPostsPollInterval(undefined)).toBe(false);
  });
});
