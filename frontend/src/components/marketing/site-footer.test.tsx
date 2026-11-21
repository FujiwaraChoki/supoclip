import { render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it } from "vitest";

import english from "../../../messages/en/marketing.json";
import { COMPANY_NAME, COMPANY_PHONE, COMPANY_REGISTERED_ADDRESS } from "@/lib/site";
import { SiteFooter } from "./site-footer";

describe("SiteFooter", () => {
  it("displays the operating company name, registered address, and phone number", () => {
    render(
      <NextIntlClientProvider locale="en" messages={{ marketing: english }}>
        <SiteFooter />
      </NextIntlClientProvider>,
    );

    expect(screen.getByText(`SupoClip is operated by ${COMPANY_NAME}.`)).toBeVisible();
    expect(screen.getByText(COMPANY_REGISTERED_ADDRESS)).toBeVisible();
    expect(screen.getByText(COMPANY_PHONE)).toBeVisible();
  });
});
