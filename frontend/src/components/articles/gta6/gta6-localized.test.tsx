import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ClipAnatomy,
  LaunchWeekTable,
  ReactionLayoutFigure,
  RulesBoard,
  WorkflowDiagram,
} from "./diagrams";
import { EarningsCalculator } from "./earnings-calculator";
import { MomentMap } from "./moment-map";
import { getGta6Copy, type GuideLocale } from "@/lib/localized-guides";

beforeEach(() => {
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("localized GTA 6 interactive components", () => {
  const locales: GuideLocale[] = ["en", "es", "pt", "id"];

  it.each(locales)("renders localized EarningsCalculator and MomentMap in %s", (locale) => {
    const copy = getGta6Copy(locale);
    render(
      <div>
        <EarningsCalculator locale={locale} />
        <MomentMap locale={locale} />
        <WorkflowDiagram locale={locale} />
        <ReactionLayoutFigure locale={locale} />
        <ClipAnatomy locale={locale} />
        <RulesBoard locale={locale} />
        <LaunchWeekTable locale={locale} />
      </div>,
    );

    expect(screen.getByText(copy.calculator.title)).toBeVisible();
    expect(screen.getByText(copy.momentMap.title)).toBeVisible();
    expect(screen.getByText(copy.workflowDiagram.caption)).toBeVisible();
    expect(screen.getByText(copy.reactionLayout.figcaption)).toBeVisible();
    expect(screen.getByText(copy.clipAnatomy.title)).toBeVisible();
    expect(screen.getByText(copy.rulesBoard.caption)).toBeVisible();
    expect(screen.getByText(copy.launchWeekTable.caption)).toBeVisible();

    // Selecting the first moment updates the live callout in the active locale.
    const firstMoment = copy.momentMap.moments[0];
    const firstButton = screen.getByRole("button", { name: new RegExp(firstMoment.title) });
    fireEvent.click(firstButton);
    expect(screen.getByText(firstMoment.note)).toBeVisible();
  });
});
