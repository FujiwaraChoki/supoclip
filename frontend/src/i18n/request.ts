import { cookies, headers } from "next/headers";
import { getRequestConfig } from "next-intl/server";

import { isLocale, LOCALE_COOKIE, negotiateLocale } from "./config";
import { loadMessages } from "./load-messages";

export default getRequestConfig(async () => {
  const cookieStore = await cookies();
  const cookieLocale = cookieStore.get(LOCALE_COOKIE)?.value;
  const locale = isLocale(cookieLocale)
    ? cookieLocale
    : negotiateLocale((await headers()).get("accept-language"));

  return {
    locale,
    messages: await loadMessages(locale),
  };
});
