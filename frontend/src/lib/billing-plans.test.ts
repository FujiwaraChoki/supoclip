import { socialPublishingStage } from "./billing-plans";

describe("socialPublishingStage", () => {
  const env = process.env;
  afterEach(() => {
    process.env = env;
  });

  it.each([
    [undefined, "soon"],
    ["", "soon"],
    ["soon", "soon"],
    ["off", "off"],
    ["0", "off"],
    ["live", "live"],
    ["1", "live"],
  ])("maps %j to %s", (value, stage) => {
    process.env = { ...env, NEXT_PUBLIC_SOCIAL_PUBLISHING: value };
    if (value === undefined) delete process.env.NEXT_PUBLIC_SOCIAL_PUBLISHING;
    expect(socialPublishingStage()).toBe(stage);
  });
});
