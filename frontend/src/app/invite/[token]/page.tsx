"use client";

import { use, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import Image from "next/image";
import { useTranslations } from "next-intl";
import {
  AlertCircle,
  ArrowRight,
  CheckCircle2,
  Clock,
  Loader2,
  LogOut,
  Users,
  XCircle,
} from "lucide-react";
import { toast } from "sonner";
import { signOut, useSession } from "@/lib/auth-client";
import { invalidateWorkspaces } from "@/hooks/use-workspace";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";

interface InvitationDetail {
  teamId: string;
  teamName: string;
  email: string;
  role: "admin" | "member";
  status: "valid" | "accepted" | "revoked" | "expired";
  inviterName: string | null;
  expiresAt: string;
}

interface InvitationResponse {
  invitation: InvitationDetail;
  signedIn: boolean;
  signedInEmail: string | null;
  emailMatches: boolean;
}

const invitationKeys = {
  /** Per viewer: the answer says whether they are signed in and as whom. */
  detail: (token: string, viewer: string) => ["team-invitation", token, viewer] as const,
};

/** The invitation behind `token`; null when it does not exist (404). */
async function fetchInvitation(token: string, loadFailed: string): Promise<InvitationResponse | null> {
  const response = await fetch(`/api/teams/invitations/${encodeURIComponent(token)}`, {
    cache: "no-store",
  });
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(loadFailed);
  return (await response.json()) as InvitationResponse;
}

export default function AcceptInvitePage({
  params,
}: {
  params: Promise<{ token: string }>;
}) {
  const { token } = use(params);
  const t = useTranslations("team");
  const [signingOut, setSigningOut] = useState(false);
  const [acceptError, setAcceptError] = useState<string | null>(null);

  const { data: session, isPending: sessionPending } = useSession();
  const viewer = session?.user?.id ?? "anon";

  // Read once per visit and viewer: a refetch on focus could swap the page under someone about
  // to join, and coming back signed in (or as someone else) must ask again, not reuse the cache.
  const invite = useQuery({
    queryKey: invitationKeys.detail(token, viewer),
    queryFn: () => fetchInvitation(token, t("invite.loadFailed")),
    enabled: !sessionPending,
    staleTime: Infinity,
    gcTime: 0,
    refetchOnWindowFocus: false,
  });
  const data = invite.data ?? null;
  const notFound = invite.data === null;
  // Also while "Try again" reloads a failed invitation.
  const loading = invite.data === undefined && (invite.isPending || invite.isFetching);
  const errorMessage = acceptError ?? (invite.isError ? invite.error.message || t("invite.loadFailed") : null);
  const loadInvite = () => {
    setAcceptError(null);
    return invite.refetch();
  };

  // A click, never on load: accepting joins the team.
  const accept = useMutation({
    mutationFn: async () => {
      const response = await fetch(
        `/api/teams/invitations/${encodeURIComponent(token)}/accept`,
        { method: "POST" },
      );
      const payload = (await response.json().catch(() => ({}))) as {
        error?: string;
        code?: string;
      };
      if (!response.ok) {
        throw new Error(payload.error || t("invite.acceptFailed"));
      }
    },
    onMutate: () => setAcceptError(null),
    onSuccess: () => {
      invalidateWorkspaces();
      toast.success(t("invite.joinedToast", { team: data?.invitation.teamName ?? "" }));
      window.location.assign("/");
    },
    onError: (error) => setAcceptError(error instanceof Error ? error.message : t("invite.acceptFailed")),
  });
  // Stays on after success: the page is navigating away.
  const accepting = accept.isPending || accept.isSuccess;
  const handleAccept = () => accept.mutate();

  const handleSignOut = async () => {
    setSigningOut(true);
    try {
      await signOut();
      window.location.reload();
    } catch {
      toast.error(t("invite.signOutFailed"));
      setSigningOut(false);
    }
  };

  const invitePath = `/invite/${encodeURIComponent(token)}`;
  const emailQuery = data?.invitation.email
    ? `&email=${encodeURIComponent(data.invitation.email)}`
    : "";

  return (
    <main className="flex min-h-dvh flex-col items-center justify-center bg-canvas px-4 py-16">
      <div className="w-full max-w-md rounded-2xl border bg-background p-6 shadow-xs sm:p-8">
        <Link href="/" className="mb-6 inline-flex items-center gap-2 font-display text-lg font-bold tracking-tight">
          <Image src="/logo.png" alt="SupoClip" width={24} height={24} className="size-6 dark:invert" />
          SupoClip
        </Link>

        {loading ? (
          <div className="space-y-4">
            <Skeleton className="h-7 w-48" />
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : notFound ? (
          <div className="space-y-4">
            <div className="flex size-10 items-center justify-center rounded-xl bg-muted text-muted-foreground">
              <XCircle className="size-5" />
            </div>
            <h1 className="font-display text-2xl font-bold tracking-tight">
              {t("invite.notFoundTitle")}
            </h1>
            <p className="text-sm text-muted-foreground">{t("invite.notFoundBody")}</p>
            <Button asChild variant="outline" className="w-full">
              <Link href="/">{t("invite.backHome")}</Link>
            </Button>
          </div>
        ) : !data ? (
          <div className="space-y-4">
            <div className="flex size-10 items-center justify-center rounded-xl bg-destructive/10 text-destructive">
              <AlertCircle className="size-5" />
            </div>
            <h1 className="font-display text-2xl font-bold tracking-tight">
              {t("invite.errorTitle")}
            </h1>
            <p className="text-sm text-muted-foreground">
              {errorMessage || t("invite.loadFailed")}
            </p>
            <Button type="button" onClick={() => void loadInvite()} className="w-full">
              {t("invite.retry")}
            </Button>
          </div>
        ) : data.invitation.status === "accepted" ? (
          <div className="space-y-4">
            <div className="flex size-10 items-center justify-center rounded-xl bg-emerald-500/10 text-emerald-600">
              <CheckCircle2 className="size-5" />
            </div>
            <h1 className="font-display text-2xl font-bold tracking-tight">
              {t("invite.acceptedTitle")}
            </h1>
            <p className="text-sm text-muted-foreground">
              {t("invite.acceptedBody", { team: data.invitation.teamName })}
            </p>
            <Button asChild className="w-full">
              <Link href="/">{t("invite.openApp")}</Link>
            </Button>
          </div>
        ) : data.invitation.status === "revoked" ? (
          <div className="space-y-4">
            <div className="flex size-10 items-center justify-center rounded-xl bg-muted text-muted-foreground">
              <XCircle className="size-5" />
            </div>
            <h1 className="font-display text-2xl font-bold tracking-tight">
              {t("invite.revokedTitle")}
            </h1>
            <p className="text-sm text-muted-foreground">
              {t("invite.revokedBody", { team: data.invitation.teamName })}
            </p>
            <Button asChild variant="outline" className="w-full">
              <Link href="/">{t("invite.backHome")}</Link>
            </Button>
          </div>
        ) : data.invitation.status === "expired" ? (
          <div className="space-y-4">
            <div className="flex size-10 items-center justify-center rounded-xl bg-amber-500/10 text-amber-600">
              <Clock className="size-5" />
            </div>
            <h1 className="font-display text-2xl font-bold tracking-tight">
              {t("invite.expiredTitle")}
            </h1>
            <p className="text-sm text-muted-foreground">
              {t("invite.expiredBody", { team: data.invitation.teamName })}
            </p>
            <Button asChild variant="outline" className="w-full">
              <Link href="/">{t("invite.backHome")}</Link>
            </Button>
          </div>
        ) : (
          <div className="space-y-5">
            <div className="flex size-11 items-center justify-center rounded-xl bg-brand/15 text-brand">
              <Users className="size-5" />
            </div>
            <div>
              <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                {t("invite.eyebrow")}
              </p>
              <h1 className="mt-1 font-display text-2xl font-bold tracking-tight">
                {t("invite.title", { team: data.invitation.teamName })}
              </h1>
              <p className="mt-2 text-sm text-muted-foreground">
                {data.invitation.inviterName
                  ? t("invite.invitedBy", {
                      inviter: data.invitation.inviterName,
                      team: data.invitation.teamName,
                    })
                  : t("invite.invitedGeneric", { team: data.invitation.teamName })}
              </p>
            </div>

            <div className="space-y-2 rounded-xl border bg-muted/40 p-4 text-sm">
              <div className="flex items-center justify-between gap-2">
                <span className="text-muted-foreground">{t("invite.emailLabel")}</span>
                <span className="truncate font-medium">{data.invitation.email}</span>
              </div>
              <div className="flex items-center justify-between gap-2">
                <span className="text-muted-foreground">{t("invite.roleLabel")}</span>
                <Badge variant="secondary">{t(`roles.${data.invitation.role}`)}</Badge>
              </div>
            </div>

            {errorMessage && (
              <div className="flex items-start gap-2 rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-700 dark:border-red-900/60 dark:bg-red-950/40 dark:text-red-300">
                <AlertCircle className="mt-0.5 size-4 shrink-0" />
                <span>{errorMessage}</span>
              </div>
            )}

            {!data.signedIn ? (
              <div className="space-y-2.5">
                <p className="text-xs text-muted-foreground">
                  {t("invite.signInHint", { email: data.invitation.email })}
                </p>
                <Button asChild size="lg" className="w-full">
                  <Link href={`/sign-up?next=${encodeURIComponent(invitePath)}${emailQuery}`}>
                    {t("invite.createAccount")}
                    <ArrowRight className="size-4" />
                  </Link>
                </Button>
                <Button asChild variant="outline" size="lg" className="w-full">
                  <Link href={`/sign-in?next=${encodeURIComponent(invitePath)}${emailQuery}`}>
                    {t("invite.signIn")}
                  </Link>
                </Button>
              </div>
            ) : !data.emailMatches ? (
              <div className="space-y-3">
                <p className="rounded-xl border border-amber-300/60 bg-amber-50/70 p-3 text-xs text-amber-900 dark:border-amber-800/60 dark:bg-amber-950/30 dark:text-amber-200">
                  {t("invite.wrongAccount", {
                    signedInEmail: data.signedInEmail ?? "",
                    invitedEmail: data.invitation.email,
                  })}
                </p>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => void handleSignOut()}
                  disabled={signingOut}
                  className="w-full"
                >
                  {signingOut ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : (
                    <LogOut className="size-4" />
                  )}
                  {t("invite.switchAccount")}
                </Button>
              </div>
            ) : (
              <Button
                type="button"
                size="lg"
                onClick={() => void handleAccept()}
                disabled={accepting}
                className="w-full"
              >
                {accepting && <Loader2 className="size-4 animate-spin" />}
                {accepting
                  ? t("invite.joining")
                  : t("invite.joinButton", { team: data.invitation.teamName })}
              </Button>
            )}
          </div>
        )}
      </div>
    </main>
  );
}
