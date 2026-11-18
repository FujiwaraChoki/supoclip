"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";
import { FeedbackWebhookManager } from "@/components/admin/feedback-webhook-manager";
import { useSession } from "@/lib/auth-client";

export default function FeedbackWebhooksPage() {
  const t = useTranslations("admin");
  const { data: session, isPending } = useSession();
  if (isPending) return <main className="p-8 text-sm text-muted-foreground">{t("feedback.webhooks.loading")}</main>;
  if (!(session?.user as { is_admin?: boolean } | undefined)?.is_admin) {
    return <main className="p-8"><h1 className="font-display text-2xl font-bold">{t("feedback.webhooks.title")}</h1><p className="mt-3 text-sm text-muted-foreground">{t("gate.notAdmin")}</p></main>;
  }
  return (
    <main className="mx-auto w-full max-w-4xl space-y-6 px-4 py-8 sm:px-8">
      <div>
        <Link href="/admin/feedback" className="text-sm text-muted-foreground hover:underline">{t("feedback.backToInbox")}</Link>
        <h1 className="mt-3 font-display text-3xl font-bold tracking-tight">{t("feedback.webhooks.title")}</h1>
      </div>
      <FeedbackWebhookManager />
    </main>
  );
}
