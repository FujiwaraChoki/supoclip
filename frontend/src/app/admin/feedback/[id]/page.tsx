import Link from "next/link";
import { notFound } from "next/navigation";
import { getLocale, getTranslations } from "next-intl/server";

import { FeedbackTriage } from "@/components/admin/feedback-triage";
import { intlLocale } from "@/i18n/config";
import { formatBillingPlanName } from "@/lib/billing-plans";
import type { FeedbackSubmission } from "@/lib/feedback-inbox";
import { fetchBackend } from "@/server/backend-api";
import { getServerSession } from "@/server/session";

export default async function FeedbackDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const t = await getTranslations("admin");
  const dateLocale = intlLocale(await getLocale());
  const session = await getServerSession();
  if (!(session?.user as { is_admin?: boolean } | undefined)?.is_admin) {
    return (
      <main className="mx-auto max-w-md px-4 py-24 text-center">
        <h1 className="font-display text-3xl font-bold tracking-tight">{t("gate.title")}</h1>
        <p className="mt-3 text-sm text-muted-foreground">{session?.user ? t("gate.notAdmin") : t("gate.signIn")}</p>
      </main>
    );
  }

  const { id } = await params;
  if (!/^[A-Za-z0-9_-]{1,64}$/.test(id)) notFound();
  const response = await fetchBackend(`/admin/feedback/${id}`, { userId: session!.user.id, cache: "no-store" });
  if (response.status === 404) notFound();
  if (!response.ok) {
    return <main className="p-8"><p role="alert" className="text-sm text-red-600">{t("feedback.loadFailed")}</p></main>;
  }
  const { submission } = (await response.json()) as { submission: FeedbackSubmission };
  const when = (iso: string) => new Date(iso).toLocaleString(dateLocale, { dateStyle: "medium", timeStyle: "short" });

  const details: [string, React.ReactNode][] = [
    [t("feedback.detail.received"), <time key="t" dateTime={submission.created_at}>{when(submission.created_at)}</time>],
    [t("feedback.detail.platform"), [submission.platform === "ios" ? "iOS" : t("feedback.detail.web"), submission.app_version].filter(Boolean).join(" ")],
    [t("feedback.detail.page"), submission.page_url || "—"],
    [t("feedback.detail.locale"), submission.locale || "—"],
    [t("feedback.detail.userAgent"), <span key="ua" className="break-all">{submission.user_agent || "—"}</span>],
  ];

  return (
    <main className="mx-auto w-full max-w-5xl space-y-6 px-4 py-8 sm:px-8 md:py-10">
      <div>
        <Link href="/admin/feedback" className="text-sm text-muted-foreground hover:underline">{t("feedback.backToInbox")}</Link>
        <h1 className="mt-3 font-display text-3xl font-bold tracking-tight">{t(`feedback.category.${submission.category}`)}</h1>
        <p className="mt-1 font-mono text-xs text-muted-foreground">{submission.id}</p>
      </div>

      <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
        <div className="space-y-6">
          <section aria-labelledby="feedback-message" className="rounded-2xl border bg-background p-5">
            <h2 id="feedback-message" className="text-sm font-semibold">{t("feedback.detail.message")}</h2>
            <p className="mt-3 whitespace-pre-wrap break-words text-sm leading-relaxed">{submission.message}</p>
          </section>
          <FeedbackTriage submission={submission} />
        </div>

        <aside className="space-y-6">
          <section aria-labelledby="feedback-sender" className="rounded-2xl border bg-background p-5">
            <h2 id="feedback-sender" className="text-sm font-semibold">{t("feedback.detail.sender")}</h2>
            {submission.user_id ? (
              <div className="mt-3 space-y-1 text-sm">
                <p className="font-medium">{submission.user_name || t("unnamedUser")}</p>
                <p className="break-all text-muted-foreground">{submission.email}</p>
                {submission.user_current_email && submission.user_current_email !== submission.email && (
                  <p className="text-xs text-muted-foreground">{t("feedback.detail.emailNow", { email: submission.user_current_email })}</p>
                )}
                {submission.user_plan && <p className="text-xs text-muted-foreground">{t("feedback.detail.plan", { plan: formatBillingPlanName(submission.user_plan) })}</p>}
                <Link href={`/admin?user=${encodeURIComponent(submission.user_id)}`} className="mt-2 inline-block text-sm font-medium text-brand underline-offset-4 hover:underline">
                  {t("feedback.detail.openUser")}
                </Link>
              </div>
            ) : (
              <p className="mt-3 text-sm text-muted-foreground">{submission.email || t("feedback.detail.noAccount")}</p>
            )}
          </section>
          <section aria-labelledby="feedback-context" className="rounded-2xl border bg-background p-5">
            <h2 id="feedback-context" className="text-sm font-semibold">{t("feedback.detail.context")}</h2>
            <dl className="mt-3 space-y-2 text-sm">
              {details.map(([label, value]) => (
                <div key={label}>
                  <dt className="text-xs text-muted-foreground">{label}</dt>
                  <dd>{value}</dd>
                </div>
              ))}
            </dl>
          </section>
        </aside>
      </div>
    </main>
  );
}
