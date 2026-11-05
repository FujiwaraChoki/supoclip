import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it } from "vitest";

import english from "../../../messages/en/marketing.json";
import spanish from "../../../messages/es/marketing.json";
import { getSupportedLanguageLabels } from "@/lib/supported-languages";
import { SupportedLanguages } from "./supported-languages";

function renderLanguages(locale = "en", marketing = english) {
  render(
    <NextIntlClientProvider locale={locale} messages={{ marketing }}>
      <SupportedLanguages languages={getSupportedLanguageLabels(locale, marketing.faqPage.mandarin)} />
    </NextIntlClientProvider>,
  );
  return { user: userEvent.setup(), search: screen.getByRole("searchbox") };
}

describe("supported language search", () => {
  it("finds native names and restores the full list after an empty result", async () => {
    const { user, search } = renderLanguages();
    const originalCount = screen.getAllByRole("listitem").length;
    await user.type(search, "日本語");
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
    expect(within(screen.getByRole("list")).getByText("Japanese")).toBeVisible();

    await user.clear(search);
    await user.type(search, "not-a-language");
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
    expect(screen.getByText(english.faqPage.noResults)).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent(`0 of ${originalCount}`);

    await user.clear(search);
    expect(screen.getAllByRole("listitem")).toHaveLength(originalCount);
  });

  it("accepts English names when the page is translated", async () => {
    const { user, search } = renderLanguages("es", spanish);
    await user.type(search, "Swiss German");
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
    expect(within(screen.getByRole("list")).getByText(/alemán suizo/i)).toBeVisible();

    await user.clear(search);
    await user.type(search, "mandarin");
    expect(within(screen.getByRole("list")).getByText("Chino (mandarín)")).toBeVisible();
  });

  it("ignores accents, casing, and surrounding whitespace", async () => {
    const { user, search } = renderLanguages("es", spanish);
    await user.type(search, "  HUNGARO  ");
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
    expect(within(screen.getByRole("list")).getByText("húngaro")).toBeVisible();
  });
});
