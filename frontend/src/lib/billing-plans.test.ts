import {
  billingPlanMessageKey,
  calculateTeamPricing,
  formatBillingPlanName,
  formatCurrencyAmount,
  getNextBillingPlan,
  getTeamBillingConfig,
  isPaidBillingPlan,
  MAX_TEAM_SEATS,
  socialPublishingStage,
} from "./billing-plans";

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

describe("team billing pricing & helpers", () => {
  it("computes base and additional seat pricing and pooled hours for monthly and annual billing", () => {
    const config = getTeamBillingConfig();
    expect(config.includedSeats).toBe(3);
    expect(config.baseMinuteLimit).toBe(3600);
    expect(config.extraSeatMinuteLimit).toBe(900);

    const baseMonthly = calculateTeamPricing(3, "month", config);
    expect(baseMonthly.seats).toBe(3);
    expect(baseMonthly.extraSeats).toBe(0);
    expect(baseMonthly.totalPrice).toBe(69);
    expect(baseMonthly.totalMinutes).toBe(3600);
    expect(baseMonthly.totalHours).toBe(60);

    const fiveSeatsMonthly = calculateTeamPricing(5, "month", config);
    expect(fiveSeatsMonthly.seats).toBe(5);
    expect(fiveSeatsMonthly.extraSeats).toBe(2);
    expect(fiveSeatsMonthly.extraSeatsSubtotal).toBe(30);
    expect(fiveSeatsMonthly.totalPrice).toBe(99);
    expect(fiveSeatsMonthly.totalMinutes).toBe(5400);
    expect(fiveSeatsMonthly.totalHours).toBe(90);

    const fiveSeatsYearly = calculateTeamPricing(5, "year", config);
    expect(fiveSeatsYearly.totalPrice).toBe(990);
    expect(fiveSeatsYearly.annualFullPrice).toBe(1188);
    expect(fiveSeatsYearly.monthsFree).toBe(2);
  });

  it("clamps requested seat counts to [includedSeats, MAX_TEAM_SEATS]", () => {
    const config = getTeamBillingConfig();
    expect(calculateTeamPricing(1, "month", config).seats).toBe(3);
    expect(calculateTeamPricing(MAX_TEAM_SEATS + 50, "month", config).seats).toBe(MAX_TEAM_SEATS);
    expect(formatCurrencyAmount(69)).toBe("69");
    expect(formatCurrencyAmount(9.99)).toBe("9.99");
  });

  it("recognizes team in plan formatters and guards", () => {
    expect(formatBillingPlanName("team")).toBe("Team");
    expect(billingPlanMessageKey("team")).toBe("team");
    expect(isPaidBillingPlan("team")).toBe(true);
    expect(getNextBillingPlan("team")).toBeNull();
  });
});
