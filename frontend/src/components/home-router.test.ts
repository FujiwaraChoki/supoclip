import { homeScreenFor } from "./home-router";
import type { AgentStatus } from "@/lib/agent";

const status = (overrides: Partial<AgentStatus>): AgentStatus => ({
  available: true,
  reason: null,
  access: "full",
  trial: null,
  plan: "scale",
  home_mode: null,
  offer_choice: true,
  model: "claude-sonnet-5-5",
  daily_limit: 150,
  ...overrides,
});

const pro = (used: number, overrides: Partial<AgentStatus> = {}) =>
  status({
    available: used < 5,
    reason: used < 5 ? null : "trial_used",
    access: used < 5 ? "trial" : null,
    trial: { limit: 5, used },
    plan: "pro",
    offer_choice: false,
    ...overrides,
  });

describe("homeScreenFor", () => {
  it("shows the plain form when the agent is switched off or not set up", () => {
    expect(homeScreenFor(null, null)).toEqual({ screen: "form" });
    expect(homeScreenFor(status({ available: false, reason: "disabled", offer_choice: false }), null)).toEqual({ screen: "form" });
    expect(homeScreenFor(status({ available: false, reason: "not_configured", offer_choice: false }), null)).toEqual({ screen: "form" });
  });

  it("asks once, then opens what the person chose", () => {
    expect(homeScreenFor(undefined, null)).toEqual({ screen: "loading" });
    expect(homeScreenFor(status({}), null)).toEqual({ screen: "choice" });
    expect(homeScreenFor(status({ home_mode: "agent", offer_choice: false }), null)).toEqual({ screen: "agent", manualHref: "/?mode=manual" });
    expect(homeScreenFor(status({ home_mode: "manual", offer_choice: false }), null)).toEqual({ screen: "form", agentLink: "/?mode=agent" });
  });

  it("opens the other one just this time from the link under the composer", () => {
    expect(homeScreenFor(status({ home_mode: "agent" }), "manual")).toEqual({ screen: "form", agentLink: "/" });
    expect(homeScreenFor(status({ home_mode: "manual" }), "agent")).toEqual({ screen: "agent", manualHref: "/" });
    // Before choosing, the one-time link skips the question.
    expect(homeScreenFor(status({}), "manual")).toEqual({ screen: "form", agentLink: "/" });
  });

  it("asks new free accounts too, then keeps the agent locked behind Creator", () => {
    const free = status({ available: false, reason: "plan", access: null, plan: "free" });
    expect(homeScreenFor(free, null)).toEqual({ screen: "choice" });
    expect(homeScreenFor({ ...free, home_mode: "manual", offer_choice: false }, null)).toEqual({
      screen: "form",
      agentLocked: { reason: "plan", lapsed: false },
    });
    expect(homeScreenFor({ ...free, offer_choice: false }, "agent")).toEqual({
      screen: "form",
      agentLocked: { reason: "plan", lapsed: false },
    });
  });

  it("leaves existing Pro accounts on the form with the trial one tap away", () => {
    expect(homeScreenFor(pro(0), null)).toEqual({ screen: "form", agentLink: "/?mode=agent" });
    expect(homeScreenFor(pro(2), "agent")).toEqual({ screen: "agent", manualHref: "/" });
    expect(homeScreenFor(pro(2, { home_mode: "agent" }), null)).toEqual({ screen: "agent", manualHref: "/?mode=manual" });
  });

  it("returns to the form when the trial is used or Creator lapses", () => {
    expect(homeScreenFor(pro(5, { home_mode: "agent" }), null)).toEqual({
      screen: "form",
      agentLocked: { reason: "trial_used", lapsed: true },
    });
    const lapsed = status({ available: false, reason: "plan", access: null, plan: "free", home_mode: "agent", offer_choice: false });
    expect(homeScreenFor(lapsed, null)).toEqual({ screen: "form", agentLocked: { reason: "plan", lapsed: true } });
  });
});
