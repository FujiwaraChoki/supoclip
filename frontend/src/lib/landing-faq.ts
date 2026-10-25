import { getPublicBillingPlans } from "@/lib/billing-plans";

type Translate = (key: string, values?: Record<string, string | number>) => string;

const FAQ_KEYS = ["sources", "selection", "minutes", "publishing", "watermark", "languages", "selfHost", "cancel"] as const;

/** Landing page FAQ, shared by the page and its FAQPage structured data. */
export function getLandingFaqs(t: Translate) {
  const [pro, scale] = getPublicBillingPlans();
  const values = {
    proHours: Math.round((pro?.minuteLimit ?? 600) / 60),
    creatorHours: Math.round((scale?.minuteLimit ?? 1500) / 60),
  };
  return FAQ_KEYS.map((key) => ({
    key,
    question: t(`faq.items.${key}.question`),
    answer: t(`faq.items.${key}.answer`, values),
  }));
}
