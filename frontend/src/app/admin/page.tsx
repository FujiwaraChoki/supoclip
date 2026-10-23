import Link from "next/link";
import { headers } from "next/headers";
import { getLocale, getTranslations } from "next-intl/server";
import { auth } from "@/lib/auth";
import prisma from "@/lib/prisma";
import { AdminUserToggle } from "@/components/admin/admin-user-toggle";
import { LegacyHdToggle } from "@/components/admin/legacy-hd-toggle";
import { formatBillingPlanName } from "@/lib/billing-plans";
import { AffiliateReviewActions } from "@/components/admin/affiliate-review-actions";
import {
  RuntimeSettingsForm,
  type RuntimeSetting,
} from "@/components/admin/runtime-settings-form";
import { Badge } from "@/components/ui/badge";
import { StatusBadge } from "@/components/app/status-badge";
import { cn } from "@/lib/utils";
import { intlLocale } from "@/i18n/config";
import { APP_STORE_ID } from "@/lib/site";
import { fetchBackend } from "@/server/backend-api";

const ACTIVE_TASK_STATUSES = ["queued", "processing", "pending"];
/** The Pro subscription's "Creator offer" in App Store Connect; add each creator's custom code there. */
const APP_STORE_CREATOR_OFFER_URL = `https://appstoreconnect.apple.com/apps/${APP_STORE_ID}/distribution/subscriptions/6786756790/pricing/offer-codes/e3b69863-0319-4852-a9c8-204bd84a8183`;

const th = "px-5 py-2.5 text-left text-[11px] font-medium uppercase tracking-wider text-muted-foreground";
const td = "px-5 py-3 text-sm";

function Panel({ title, description, action, children }: { title: string; description: string; action?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="overflow-hidden rounded-2xl border bg-background">
      <div className="flex items-center justify-between gap-4 border-b px-5 py-4">
        <div>
          <h2 className="text-sm font-semibold">{title}</h2>
          <p className="text-xs text-muted-foreground">{description}</p>
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

function TaskCell({ id, title, untitled }: { id: string; title?: string | null; untitled: string }) {
  return (
    <td className={td}>
      <Link href={`/tasks/${id}`} className="block max-w-[420px] truncate font-medium hover:underline">{title || untitled}</Link>
      <span className="font-mono text-[11px] text-muted-foreground">{id.slice(0, 8)}</span>
    </td>
  );
}

function Gate({ title, message, children }: { title: string; message: string; children?: React.ReactNode }) {
  return (
    <main className="mx-auto max-w-md px-4 py-24 text-center">
      <h1 className="font-display text-3xl font-bold tracking-tight">{title}</h1>
      <p className="mt-3 text-sm text-muted-foreground">{message}</p>
      {children}
    </main>
  );
}

export default async function AdminPage({
  searchParams,
}: {
  searchParams: Promise<{ user?: string; q?: string }>;
}) {
  const t = await getTranslations("admin");
  const dateLocale = intlLocale(await getLocale());
  const session = await auth.api.getSession({ headers: await headers() });

  if (!session?.user) {
    return (
      <Gate title={t("gate.title")} message={t("gate.signIn")}>
        <Link href="/sign-in" className="mt-6 inline-block text-sm font-medium underline underline-offset-4">{t("gate.goToSignIn")}</Link>
      </Gate>
    );
  }

  const isAdmin = Boolean((session.user as { is_admin?: boolean }).is_admin);

  if (!isAdmin) {
    return <Gate title={t("gate.title")} message={t("gate.notAdmin")} />;
  }

  const { user: selectedUserId, q } = await searchParams;
  const userQuery = q?.trim() || "";
  const adminUserId = session.user.id;

  async function loadRuntimeSettings(): Promise<{
    settings: RuntimeSetting[];
    error: string | null;
  }> {
    try {
      const response = await fetchBackend("/admin/runtime-settings", {
        method: "GET",
        userId: adminUserId,
        cache: "no-store",
      });
      if (!response.ok) {
        return { settings: [], error: t("runtime.loadFailed") };
      }
      const payload = (await response.json()) as { settings?: RuntimeSetting[] };
      return { settings: payload.settings ?? [], error: null };
    } catch {
      return { settings: [], error: t("runtime.unreachable") };
    }
  }

  const [
    runtimeSettings,
    totalUsers,
    adminUsers,
    totalTasks,
    completedTasks,
    activeTasks,
    recentUsers,
    processingNow,
    recentGenerations,
    tasksByUser,
    selectedUser,
    selectedUserTasks,
    affiliates,
  ] = await Promise.all([
    loadRuntimeSettings(),
    prisma.user.count(),
    prisma.user.count({ where: { is_admin: true } }),
    prisma.task.count(),
    prisma.task.count({ where: { status: "completed" } }),
    prisma.task.count({ where: { status: { in: ACTIVE_TASK_STATUSES } } }),
    prisma.user.findMany({
      where: userQuery
        ? {
            OR: [
              { email: { contains: userQuery, mode: "insensitive" } },
              { name: { contains: userQuery, mode: "insensitive" } },
              { id: userQuery },
            ],
          }
        : undefined,
      orderBy: { createdAt: "desc" },
      take: 30,
      select: {
        id: true,
        email: true,
        name: true,
        is_admin: true,
        plan: true,
        subscription_status: true,
        subscription_provider: true,
        legacy_hd: true,
        createdAt: true,
      },
    }),
    prisma.task.findMany({
      where: { status: { in: ACTIVE_TASK_STATUSES } },
      orderBy: { updated_at: "desc" },
      take: 25,
      select: {
        id: true,
        status: true,
        created_at: true,
        updated_at: true,
        user: {
          select: {
            id: true,
            email: true,
          },
        },
        source: {
          select: {
            title: true,
          },
        },
      },
    }),
    prisma.task.findMany({
      orderBy: { created_at: "desc" },
      take: 40,
      select: {
        id: true,
        status: true,
        created_at: true,
        generated_clips_ids: true,
        user: {
          select: {
            id: true,
            email: true,
          },
        },
        source: {
          select: {
            title: true,
            type: true,
          },
        },
      },
    }),
    prisma.task.groupBy({
      by: ["user_id"],
      _count: {
        _all: true,
      },
    }),
    selectedUserId
      ? prisma.user.findUnique({
          where: { id: selectedUserId },
          select: {
            id: true,
            email: true,
            name: true,
            is_admin: true,
          },
        })
      : Promise.resolve(null),
    selectedUserId
      ? prisma.task.findMany({
          where: { user_id: selectedUserId },
          orderBy: { created_at: "desc" },
          take: 40,
          select: {
            id: true,
            status: true,
            created_at: true,
            generated_clips_ids: true,
            source: {
              select: {
                title: true,
                type: true,
              },
            },
          },
        })
      : Promise.resolve([]),
    prisma.affiliate.findMany({
      where: { status: { in: ["pending", "approved"] } },
      orderBy: { created_at: "desc" },
      take: 100,
      select: {
        id: true,
        slug: true,
        status: true,
        platform: true,
        profile_url: true,
        audience_size: true,
        video_url: true,
        promotion_plan: true,
        app_store_code_added_at: true,
        created_at: true,
        user: { select: { email: true, name: true } },
      },
    }),
  ]);

  // Pending applications first; each group newest first.
  const sortedAffiliates = [...affiliates].sort(
    (a, b) => Number(b.status === "pending") - Number(a.status === "pending"),
  );
  const pendingAffiliates = affiliates.filter((affiliate) => affiliate.status === "pending").length;

  const generationCountByUser = new Map(tasksByUser.map((item) => [item.user_id, item._count._all]));

  const stats = [
    { label: t("stats.users"), value: totalUsers },
    { label: t("stats.admins"), value: adminUsers },
    { label: t("stats.tasks"), value: totalTasks },
    { label: t("stats.completed"), value: completedTasks },
    { label: t("stats.processing"), value: activeTasks, live: activeTasks > 0 },
  ];

  return (
    <main className="mx-auto w-full max-w-7xl space-y-6 px-4 py-8 sm:px-8 md:py-10">
      <div>
        <h1 className="font-display text-3xl font-bold tracking-tight">{t("title")}</h1>
        <p className="mt-1 text-sm text-muted-foreground">{t("subtitle")}</p>
        <Link href="/admin/announcements" className="mt-3 inline-block text-sm font-medium text-brand underline-offset-4 hover:underline">{t("manageAnnouncements")}</Link>
      </div>

      <section className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-5">
        {stats.map((stat) => (
          <div key={stat.label} className={cn("rounded-2xl border bg-background p-4", stat.live && "border-brand/40 bg-brand-soft")}>
            <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
              {stat.live && <span className="size-1.5 rounded-full bg-brand motion-safe:animate-pulse" />}{stat.label}
            </p>
            <p className="mt-1 font-display text-3xl font-bold tabular-nums">{stat.value}</p>
          </div>
        ))}
      </section>

      <div id="affiliates" className="scroll-mt-6">
        <Panel
          title={t("creators.title")}
          description={t(pendingAffiliates === 1 ? "creators.pendingOne" : "creators.pendingOther", { count: pendingAffiliates })}
          action={<a href={APP_STORE_CREATOR_OFFER_URL} target="_blank" rel="noopener noreferrer" className="text-xs font-medium underline-offset-4 hover:underline">{t("creators.offerCodes")}</a>}
        >
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y">
              <thead className="bg-muted/40"><tr><th className={th}>{t("columns.creator")}</th><th className={th}>{t("columns.code")}</th><th className={th}>{t("columns.channel")}</th><th className={th}>{t("columns.pitch")}</th><th className={cn(th, "text-right")}>{t("columns.action")}</th></tr></thead>
              <tbody className="divide-y">
                {sortedAffiliates.length === 0 ? (
                  <tr><td className={cn(td, "text-muted-foreground")} colSpan={5}>{t("creators.empty")}</td></tr>
                ) : sortedAffiliates.map((affiliate) => (
                  <tr key={affiliate.id} className="align-top hover:bg-muted/30">
                    <td className={td}>
                      <p className="flex items-center gap-2 font-medium">
                        {affiliate.user.name || t("unnamedUser")}
                        <Badge variant={affiliate.status === "pending" ? "default" : "outline"}>{t(affiliate.status === "pending" ? "creators.statusPending" : "creators.statusApproved")}</Badge>
                      </p>
                      <p className="text-xs text-muted-foreground">{t("creators.applied", { email: affiliate.user.email, date: affiliate.created_at.toLocaleDateString(dateLocale) })}</p>
                    </td>
                    <td className={cn(td, "font-mono")}>{affiliate.slug?.toUpperCase()}</td>
                    <td className={td}>
                      <a href={affiliate.profile_url} target="_blank" rel="noopener noreferrer" className="font-medium capitalize underline-offset-4 hover:underline">{affiliate.platform}</a>
                      <p className="text-xs text-muted-foreground">{t("creators.followers", { count: affiliate.audience_size })}</p>
                      {affiliate.video_url && (
                        <a href={affiliate.video_url} target="_blank" rel="noopener noreferrer" className="text-xs underline-offset-4 hover:underline">{t("creators.video")}</a>
                      )}
                    </td>
                    <td className={cn(td, "max-w-xs text-xs text-muted-foreground")}>{affiliate.promotion_plan || "—"}</td>
                    <td className={cn(td, "text-right")}>
                      <AffiliateReviewActions affiliateId={affiliate.id} status={affiliate.status} appStoreCodeAdded={Boolean(affiliate.app_store_code_added_at)} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      </div>

      <Panel title={t("processing.title")} description={t("processing.description")}>
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y">
            <thead className="bg-muted/40"><tr><th className={th}>{t("columns.task")}</th><th className={th}>{t("columns.user")}</th><th className={th}>{t("columns.status")}</th><th className={th}>{t("columns.updated")}</th></tr></thead>
            <tbody className="divide-y">
              {processingNow.length === 0 ? (
                <tr><td className={cn(td, "text-muted-foreground")} colSpan={4}>{t("processing.empty")}</td></tr>
              ) : processingNow.map((task) => (
                <tr key={task.id} className="hover:bg-muted/30">
                  <TaskCell id={task.id} title={task.source?.title} untitled={t("untitledSource")} />
                  <td className={cn(td, "text-muted-foreground")}>{task.user.email}</td>
                  <td className={td}><StatusBadge status={task.status} /></td>
                  <td className={cn(td, "whitespace-nowrap text-muted-foreground")}>{task.updated_at.toLocaleString(dateLocale)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      <div className="grid gap-6 xl:grid-cols-2">
        <Panel
          title={t("users.title")}
          description={userQuery ? t("users.descriptionSearch", { query: userQuery }) : t("users.description")}
          action={
            <form action="/admin" className="flex items-center gap-2">
              <input
                type="search"
                name="q"
                defaultValue={userQuery}
                placeholder={t("users.searchPlaceholder")}
                aria-label={t("users.searchLabel")}
                className="h-8 w-44 rounded-md border bg-background px-2.5 text-xs outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
              {userQuery && <Link href="/admin" className="text-xs font-medium underline-offset-4 hover:underline">{t("users.clearSearch")}</Link>}
            </form>
          }
        >
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y">
              <thead className="bg-muted/40"><tr><th className={th}>{t("columns.user")}</th><th className={th}>{t("columns.plan")}</th><th className={th}>{t("columns.tasks")}</th><th className={cn(th, "text-right")}>{t("columns.action")}</th></tr></thead>
              <tbody className="divide-y">
                {recentUsers.map((user) => (
                  <tr key={user.id} className={cn("hover:bg-muted/30", user.id === selectedUserId && "bg-brand-soft/60")}>
                    <td className={td}>
                      <p className="flex items-center gap-2 font-medium">
                        {user.name || t("unnamedUser")}
                        {user.is_admin && <Badge className="bg-foreground text-background">{t("users.adminBadge")}</Badge>}
                      </p>
                      <p className="text-xs text-muted-foreground">{t("users.joined", { email: user.email, date: user.createdAt.toLocaleDateString(dateLocale) })}</p>
                    </td>
                    <td className={td}>
                      <div className="flex flex-wrap items-center gap-1.5">
                        <Badge variant="outline">{formatBillingPlanName(user.plan)}</Badge>
                        {user.legacy_hd && <Badge variant="outline">{t("users.legacyBadge")}</Badge>}
                      </div>
                      {user.plan !== "free" && (
                        <p className="mt-1 text-xs text-muted-foreground">
                          {user.subscription_status}{user.subscription_provider ? ` · ${user.subscription_provider}` : ""}
                        </p>
                      )}
                    </td>
                    <td className={td}>
                      <Link href={`/admin?user=${user.id}`} className="tabular-nums underline-offset-4 hover:underline" aria-label={t("users.viewTasks", { email: user.email })}>
                        {generationCountByUser.get(user.id) || 0}
                      </Link>
                    </td>
                    <td className={cn(td, "text-right")}>
                      <div className="flex flex-col items-end gap-2">
                        <AdminUserToggle userId={user.id} isAdmin={user.is_admin} isCurrentUser={user.id === session.user.id} />
                        {/* 1080p is only grandfathered on Pro; still allow removing a stale flag. */}
                        {(user.plan === "pro" || user.legacy_hd) && <LegacyHdToggle userId={user.id} legacyHd={user.legacy_hd} />}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>

        <Panel
          title={t("explorer.title")}
          description={selectedUser ? t("explorer.viewing", { name: selectedUser.name || selectedUser.email, email: selectedUser.email }) : t("explorer.description")}
          action={selectedUserId ? <Link href="/admin" className="text-xs font-medium underline-offset-4 hover:underline">{t("explorer.clear")}</Link> : undefined}
        >
          {!selectedUser ? (
            <p className="px-5 py-10 text-center text-sm text-muted-foreground">{t("explorer.selectUser")}</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y">
                <thead className="bg-muted/40"><tr><th className={th}>{t("columns.task")}</th><th className={th}>{t("columns.status")}</th><th className={th}>{t("columns.clips")}</th><th className={th}>{t("columns.created")}</th></tr></thead>
                <tbody className="divide-y">
                  {selectedUserTasks.length === 0 ? (
                    <tr><td className={cn(td, "text-muted-foreground")} colSpan={4}>{t("explorer.empty")}</td></tr>
                  ) : selectedUserTasks.map((task) => (
                    <tr key={task.id} className="hover:bg-muted/30">
                      <TaskCell id={task.id} title={task.source?.title} untitled={t("untitledSource")} />
                      <td className={td}><StatusBadge status={task.status} /></td>
                      <td className={cn(td, "tabular-nums")}>{task.generated_clips_ids.length}</td>
                      <td className={cn(td, "whitespace-nowrap text-muted-foreground")}>{task.created_at.toLocaleString(dateLocale)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      </div>

      <Panel title={t("recent.title")} description={t("recent.description")}>
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y">
            <thead className="bg-muted/40"><tr><th className={th}>{t("columns.task")}</th><th className={th}>{t("columns.user")}</th><th className={th}>{t("columns.status")}</th><th className={th}>{t("columns.clips")}</th><th className={th}>{t("columns.created")}</th></tr></thead>
            <tbody className="divide-y">
              {recentGenerations.map((task) => (
                <tr key={task.id} className="hover:bg-muted/30">
                  <TaskCell id={task.id} title={task.source?.title} untitled={t("untitledSource")} />
                  <td className={cn(td, "text-muted-foreground")}>{task.user.email}</td>
                  <td className={td}><StatusBadge status={task.status} /></td>
                  <td className={cn(td, "tabular-nums")}>{task.generated_clips_ids.length}</td>
                  <td className={cn(td, "whitespace-nowrap text-muted-foreground")}>{task.created_at.toLocaleString(dateLocale)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      <Panel title={t("runtime.title")} description={t("runtime.description")}>
        {runtimeSettings.error ? (
          <div className="px-5 py-5 text-sm text-red-700">{runtimeSettings.error}</div>
        ) : (
          <RuntimeSettingsForm settings={runtimeSettings.settings} />
        )}
      </Panel>
    </main>
  );
}
