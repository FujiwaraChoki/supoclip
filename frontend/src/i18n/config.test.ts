import { describe, expect, it } from "vitest";

import { catalogKey, intlLocale, negotiateLocale } from "./config";

describe("negotiateLocale", () => {
  it("falls back to English when the header is missing", () => {
    expect(negotiateLocale(null)).toBe("en");
    expect(negotiateLocale("")).toBe("en");
  });

  it("picks the highest-quality supported language", () => {
    expect(negotiateLocale("fr-FR,es-MX;q=0.8,en;q=0.5")).toBe("es");
    expect(negotiateLocale("de-CH,de;q=0.9,en;q=0.8")).toBe("de");
    expect(negotiateLocale("pt-BR,pt;q=0.9")).toBe("pt");
    expect(negotiateLocale("zh-Hant-TW,zh;q=0.9,en;q=0.8")).toBe("zh");
    expect(negotiateLocale("en-US,en;q=0.9")).toBe("en");
  });
});

describe("locale helpers", () => {
  it("maps app locales onto BCP 47 tags", () => {
    expect(intlLocale("zh")).toBe("zh-Hans");
    expect(intlLocale("pt")).toBe("pt-BR");
    expect(intlLocale("de")).toBe("de");
  });

  it("builds flat catalog keys from paths", () => {
    expect(catalogKey("/blog/self-host-supoclip-docker")).toBe("blog_self_host_supoclip_docker");
  });
});
