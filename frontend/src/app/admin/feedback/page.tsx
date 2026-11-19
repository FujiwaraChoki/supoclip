import Link from "next/link";
import { getLocale, getTranslations } from "next-intl/server";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { FeedbackCategoryBadge, FeedbackStatusBadge } from "@/components/admin/feedback-badges";
import { intlLocale } from "@/i18n/config";
import {
  FEEDBACK_CATEGORIES,
  FEEDBACK_STATUSES,
  dateRangeQuery,
  type FeedbackList,
} from "@/lib/feedback-inbox";
import { fetchBackend } from "@/server/backend-api";
import { getServerSession } from "@/server/session";

const th = "px-5 py-2.5 text-left text-[11px] font-medium uppercase tracking-wider text-muted-foreground";
const td = "px-5 py-3 text-sm align-top";
const select = "h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm shadow-xs focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none";

type Search = { q?: string; category?: string; status?: string; from?: string; to?: string; page?: string };

export default async function FeedbackInboxPage({ searchParams }: { searchParams: Promise<Search> }) {
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

  const search = await searchParams;
  const page = Math.max(1, Number.parseInt(search.page ?? "1", 10) || 1);
  const query = new URLSearchParams({
    ...(search.q?.trim() ? { q: search.q.trim() } : {}),
    ...(search.category ? { category: search.category } : {}),
    ...(search.status ? { status: search.status } : {}),
    ...dateRangeQuery(search.from, search.to),
    page: String(page),
  });

  let list: FeedbackList | null = null;
  try {
    const response = await fetchBackend(`/admin/feedback?${query}`, { userId: session!.user.id, cache: "no-store" });
    if (response.ok) list = (await response.json()) as FeedbackList;
  } catch {
    list = null;
  }

  const linkWith = (changes: Partial<Search>) => {
    const next = new URLSearchParams();
    for (const [key, value] of Object.entries({ ...search, page: undefined, ...changes })) {
      if (value) next.set(key, String(value));
    }
    const qs = next.toString();
    return qs ? `/admin/feedback?${qs}` : "/admin/feedback";
  };
  const filtered = Boolean(search.q || search.category || search.status || search.from || search.to);
  const pages = list ? Math.max(1, Math.ceil(list.total / list.page_size)) : 1;

  return (
    <main className="mx-auto w-full max-w-7xl space-y-6 px-4 py-8 sm:px-8 md:py-10">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <Link href="/admin" className="text-sm text-muted-foreground hover:underline">{t("feedback.back")}</Link>
          <h1 className="mt-3 font-display text-3xl font-bold tracking-tight">{t("feedback.title")}</h1>
          <p className="mt-1 text-sm text-muted-foreground">{t("feedback.subtitle")}</p>
        </div>
        <Button asChild variant="outline">
          <Link href="/admin/feedback/webhooks">{t("feedback.manageWebhooks")}</Link>
        </Button>
      </div>

      {list && (
        <nav aria-label={t("feedback.statusFilter")} className="flex flex-wrap gap-2">
          {FEEDBACK_STATUSES.map((status) => {
            const active = search.status === status;
            return (
              <Link
                key={status}
                href={linkWith({ status: active ? undefined : status })}
                aria-current={active ? "page" : undefined}
                className={`rounded-full border px-3 py-1 text-sm ${active ? "border-brand bg-brand-soft font-medium" : "hover:bg-muted"}`}
              >
                {t(`feedback.status.${status}`)} <span className="tabular-nums text-muted-foreground">{list.status_counts[status] ?? 0}</span>
              </Link>
            );
          })}
        </nav>
      )}

      <form method="get" action="/admin/feedback" role="search" aria-label={t("feedback.filters")} className="grid gap-3 rounded-2xl border bg-background p-4 sm:grid-cols-2 lg:grid-cols-[minmax(0,2fr)_minmax(11rem,1fr)_minmax(9rem,1fr)_minmax(0,1fr)_minmax(0,1fr)_auto]">
        <label className="space-y-1 text-xs font-medium text-muted-foreground">
          {t("feedback.searchLabel")}
          <Input name="q" type="search" defaultValue={search.q ?? ""} placeholder={t("feedback.searchPlaceholder")} maxLength={200} />
        </label>
        <label className="space-y-1 text-xs font-medium text-muted-foreground">
          {t("feedback.categoryLabel")}
          <select name="category" defaultValue={search.category ?? ""} className={select}>
            <option value="">{t("feedback.allCategories")}</option>
            {FEEDBACK_CATEGORIES.map((category) => (
              <option key={category} value={category}>{t(`feedback.category.${category}`)}</option>
            ))}
          </select>
        </label>
        <label className="space-y-1 text-xs font-medium text-muted-foreground">
          {t("feedback.statusLabel")}
          <select name="status" defaultValue={search.status ?? ""} className={select}>
            <option value="">{t("feedback.allStatuses")}</option>
            {FEEDBACK_STATUSES.map((status) => (
              <option key={status} value={status}>{t(`feedback.status.${status}`)}</option>
            ))}
          </select>
        </label>
        <label className="space-y-1 text-xs font-medium text-muted-foreground">
          {t("feedback.from")}
          <Input name="from" type="date" defaultValue={search.from ?? ""} />
        </label>
        <label className="space-y-1 text-xs font-medium text-muted-foreground">
          {t("feedback.to")}
          <Input name="to" type="date" defaultValue={search.to ?? ""} />
        </label>
        <div className="flex items-end gap-2">
          <Button type="submit">{t("feedback.apply")}</Button>
          {filtered && (
            <Button asChild variant="ghost"><Link href="/admin/feedback">{t("feedback.clear")}</Link></Button>
          )}
        </div>
      </form>

      <section aria-labelledby="feedback-results" className="overflow-hidden rounded-2xl border bg-background">
        <h2 id="feedback-results" className="sr-only">{t("feedback.results")}</h2>
        {!list ? (
          <p role="alert" className="p-8 text-center text-sm text-red-600">{t("feedback.loadFailed")}</p>
        ) : !list.items.length ? (
          <p className="p-8 text-center text-sm text-muted-foreground">{filtered ? t("feedback.noMatches") : t("feedback.empty")}</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px]">
              <thead className="border-b bg-muted/40">
                <tr>
                  <th scope="col" className={th}>{t("feedback.columns.received")}</th>
                  <th scope="col" className={th}>{t("feedback.columns.from")}</th>
                  <th scope="col" className={th}>{t("feedback.columns.category")}</th>
                  <th scope="col" className={th}>{t("feedback.columns.message")}</th>
                  <th scope="col" className={th}>{t("feedback.columns.status")}</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {list.items.map((item) => (
                  <tr key={item.id} className="hover:bg-muted/30">
                    <td className={`${td} whitespace-nowrap text-muted-foreground`}>
                      <time dateTime={item.created_at}>{new Date(item.created_at).toLocaleString(dateLocale, { dateStyle: "medium", timeStyle: "short" })}</time>
                    </td>
                    <td className={td}>
                      <span className="block max-w-[220px] truncate font-medium">{item.user_name || item.email || t("feedback.anonymous")}</span>
                      {item.email && item.user_name && <span className="block max-w-[220px] truncate text-xs text-muted-foreground">{item.email}</span>}
                    </td>
                    <td className={td}><FeedbackCategoryBadge category={item.category} label={t(`feedback.category.${item.category}`)} /></td>
                    <td className={td}>
                      <Link href={`/admin/feedback/${item.id}`} className="line-clamp-2 max-w-[460px] hover:underline">{item.message}</Link>
                      <span className="mt-1 flex flex-wrap gap-2 text-xs text-muted-foreground">
                        {item.platform === "ios" && <span>iOS</span>}
                        {item.note_count > 0 && <span>{t("feedback.noteCount", { count: item.note_count })}</span>}
                        {item.failed_deliveries > 0 && <Badge variant="destructive">{t("feedback.failedDeliveries", { count: item.failed_deliveries })}</Badge>}
                      </span>
                    </td>
                    <td className={td}><FeedbackStatusBadge status={item.status} label={t(`feedback.status.${item.status}`)} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {list && pages > 1 && (
        <nav aria-label={t("feedback.pagination")} className="flex items-center justify-between text-sm">
          {page > 1 ? <Link className="font-medium hover:underline" href={linkWith({ page: String(page - 1) })}>{t("feedback.previous")}</Link> : <span />}
          <span className="text-muted-foreground">{t("feedback.pageOf", { page, pages })}</span>
          {page < pages ? <Link className="font-medium hover:underline" href={linkWith({ page: String(page + 1) })}>{t("feedback.next")}</Link> : <span />}
        </nav>
      )}
    </main>
  );
}
