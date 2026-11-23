"use client";

import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useTranslations } from "next-intl";
import {
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  Check,
  Copy,
  CreditCard,
  Crown,
  ExternalLink,
  Loader2,
  Mail,
  Minus,
  Plus,
  RefreshCw,
  Shield,
  SlidersHorizontal,
  Sparkles,
  Trash2,
  UserMinus,
  UserPlus,
  Users,
} from "lucide-react";
import { toast } from "sonner";
import { useSession } from "@/lib/auth-client";
import { monetizationEnabled } from "@/lib/monetization";
import {
  calculateTeamPricing,
  formatCurrencyAmount,
  getPublicBillingPlans,
  getTeamBillingConfig,
  MAX_TEAM_SEATS,
  socialPublishingStage,
  type BillingInterval,
} from "@/lib/billing-plans";
import { hasPaidAccess } from "@/lib/subscription-status";
import {
  announceWorkspaceSwitch,
  invalidateWorkspaces,
  useWorkspace,
  WorkspaceRequestError,
  type TeamRole,
} from "@/hooks/use-workspace";
import { BillingIntervalToggle } from "@/components/billing/upgrade-prompt";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { usePollUntil } from "@/lib/queries/settings-polling";

interface TeamMemberItem {
  userId: string;
  name: string | null;
  email: string;
  image: string | null;
  role: TeamRole;
  joinedAt: string;
  monthlyMinuteLimit?: number | null;
  monthlyAiEditLimit?: number | null;
  usageMinutes?: number | null;
  usageAiEdits?: number | null;
}

interface TeamInvitationItem {
  id: string;
  email: string;
  role: "admin" | "member";
  invitedBy: string | null;
  expiresAt: string;
  createdAt: string;
}

interface TeamUsageBilling {
  plan?: string;
  subscription_status?: string;
  billing_interval?: BillingInterval;
  usage_minutes?: number;
  minute_limit?: number | null;
  remaining_minutes?: number | null;
  member_minute_limit?: number | null;
  member_usage_minutes?: number | null;
  output_short_side?: number | null;
  period_end?: string | null;
  monetization_enabled?: boolean;
}

interface TeamUsageAiEdits {
  used?: number;
  limit?: number | null;
  remaining?: number | null;
  memberLimit?: number | null;
  memberUsed?: number | null;
}

interface TeamDetailPayload {
  team: {
    id: string;
    name: string;
    slug: string;
    ownerId: string;
    plan: string;
    subscriptionStatus: string;
    subscriptionProvider: string | null;
    seatLimit: number;
    billingPeriodStart: string | null;
    billingPeriodEnd: string | null;
    cancelAt: string | null;
    hasStripeCustomer: boolean;
    hasStripeSubscription: boolean;
    createdAt: string;
  };
  currentUserRole: TeamRole;
  permissions: {
    canManageMembers: boolean;
    canManageBilling: boolean;
    canDeleteTeam: boolean;
  };
  seats: {
    limit: number;
    activeMembers: number;
    pendingInvitations: number;
    occupied: number;
  };
  members: TeamMemberItem[];
  invitations: TeamInvitationItem[];
  usage: {
    billing?: TeamUsageBilling;
    ai_edits?: TeamUsageAiEdits;
  } | null;
}

const teamKeys = {
  // Siblings, not prefixes: refreshing the detail must not also refetch the checkout poll.
  detail: (teamId: string) => ["team", teamId, "detail"] as const,
  awaitPaid: (teamId: string) => ["team", teamId, "await-paid"] as const,
};

/** The team's settings view; throws with the server's reason when it gives one. */
async function fetchTeamDetail(teamId: string, loadFailed: string): Promise<TeamDetailPayload> {
  const response = await fetch(`/api/teams/${encodeURIComponent(teamId)}`, {
    cache: "no-store",
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => ({}))) as { error?: string };
    throw new Error(payload.error || loadFailed);
  }
  return (await response.json()) as TeamDetailPayload;
}

const isPaidTeam = (detail: TeamDetailPayload) =>
  hasPaidAccess(detail.team.plan, detail.team.subscriptionStatus, detail.team.subscriptionProvider);

function memberInitials(name?: string | null, email?: string | null) {
  const source = name?.trim() || email || "?";
  return source
    .split(/[\s@.]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("");
}

function formatShortDate(iso: string | null | undefined) {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

export default function TeamSettingsPage() {
  const t = useTranslations("team");
  const common = useTranslations("common");
  const { data: session, isPending } = useSession();
  const {
    activeTeamId,
    teams,
    loading: workspaceLoading,
    teamsAvailable,
    switchWorkspace,
    createTeam,
  } = useWorkspace(Boolean(session?.user));

  // Create team form (when in personal workspace)
  const [newTeamName, setNewTeamName] = useState("");
  const [creatingTeam, setCreatingTeam] = useState(false);

  // Rename team form
  const [teamNameDraft, setTeamNameDraft] = useState("");
  const [savingName, setSavingName] = useState(false);

  // Seats & billing state
  const teamConfig = getTeamBillingConfig();
  const [billingInterval, setBillingInterval] = useState<BillingInterval>("month");
  const [desiredSeats, setDesiredSeats] = useState<number>(teamConfig.includedSeats);
  const [billingBusy, setBillingBusy] = useState(false);

  // Invite form state
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<"member" | "admin">("member");
  const [inviting, setInviting] = useState(false);
  const [lastInviteLink, setLastInviteLink] = useState<{
    email: string;
    url: string;
    emailSent: boolean;
  } | null>(null);
  const [copiedLink, setCopiedLink] = useState(false);

  // Row-level busy state
  const [busyMemberId, setBusyMemberId] = useState<string | null>(null);
  const [busyInviteId, setBusyInviteId] = useState<string | null>(null);

  // Per-member generation limits dialog state
  const [limitsTarget, setLimitsTarget] = useState<TeamMemberItem | null>(null);
  const [minuteLimitDraft, setMinuteLimitDraft] = useState("");
  const [aiLimitDraft, setAiLimitDraft] = useState("");
  const [savingLimits, setSavingLimits] = useState(false);

  // Transfer ownership & delete dialogs
  const [transferTargetId, setTransferTargetId] = useState<string>("");
  const [transferring, setTransferring] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [deleteConfirmText, setDeleteConfirmText] = useState("");
  const [deletingTeam, setDeletingTeam] = useState(false);

  const queryClient = useQueryClient();
  const teamId = session?.user?.id && activeTeamId ? activeTeamId : null;
  const detailQuery = useQuery({
    queryKey: teamKeys.detail(teamId ?? ""),
    queryFn: () => fetchTeamDetail(teamId!, t("errors.loadFailed")),
    enabled: teamId !== null,
  });
  // A failed refresh keeps what is on screen (the query keeps its data); only a first load that fails says so.
  const detail = teamId ? detailQuery.data ?? null : null;
  const loadingDetail = !detail && (detailQuery.isPending || detailQuery.isFetching);
  const detailError = !detail && detailQuery.isError
    ? detailQuery.error.message || t("errors.loadFailed")
    : null;
  const retryDetail = () => void detailQuery.refetch();
  /** Reload the team in the background, after a change made here. */
  const refreshDetail = (id: string) => queryClient.invalidateQueries({ queryKey: teamKeys.detail(id) });

  // The detail the drafts were last synced with, to tell a draft the user changed from one
  // still showing the server's value.
  const [syncedDetail, setSyncedDetail] = useState<TeamDetailPayload | null>(null);
  if (detail && detail !== syncedDetail) {
    const previous = syncedDetail;
    setSyncedDetail(detail);
    // A refresh of the same team follows the server only where the user has not typed something else.
    const fresh = !previous || previous.team.id !== detail.team.id;
    const seatsOf = (value: TeamDetailPayload) => Math.max(teamConfig.includedSeats, value.seats.limit);
    if (fresh || teamNameDraft === previous.team.name) setTeamNameDraft(detail.team.name);
    if (fresh || desiredSeats === seatsOf(previous)) setDesiredSeats(seatsOf(detail));
    const interval = detail.usage?.billing?.billing_interval;
    if (interval && (fresh || billingInterval === (previous.usage?.billing?.billing_interval ?? billingInterval))) {
      setBillingInterval(interval);
    }
  }

  // Handle return from Stripe checkout (?billing=success): look again until the plan is paid.
  const [awaitingCheckout, setAwaitingCheckout] = useState(false);
  useEffect(() => {
    if (!activeTeamId) return;
    const params = new URLSearchParams(window.location.search);
    const outcome = params.get("billing");
    if (!outcome) return;

    params.delete("billing");
    const nextQuery = params.toString();
    window.history.replaceState(
      null,
      "",
      `${window.location.pathname}${nextQuery ? `?${nextQuery}` : ""}`,
    );

    if (outcome === "success") setAwaitingCheckout(true);
  }, [activeTeamId]);

  const checkoutPoll = usePollUntil({
    queryKey: teamKeys.awaitPaid(teamId ?? ""),
    queryFn: () => fetchTeamDetail(teamId!, "").catch(() => null),
    enabled: awaitingCheckout && teamId !== null,
    done: isPaidTeam,
    // The first look now, then 8 more every 1.5s.
    attempts: 9,
    intervalMs: 1500,
  });
  const polledDetail = checkoutPoll.data;
  const checkoutPollExhausted = checkoutPoll.exhausted;
  useEffect(() => {
    if (!awaitingCheckout) return;
    if (polledDetail) {
      // A slower detail request still in flight (the first load) must not land the unpaid team over this.
      const key = teamKeys.detail(polledDetail.team.id);
      void queryClient.cancelQueries({ queryKey: key }).then(() => queryClient.setQueryData(key, polledDetail));
    }
    if (polledDetail && isPaidTeam(polledDetail)) {
      setAwaitingCheckout(false);
      invalidateWorkspaces();
      toast.success(t("toasts.subscriptionActive"));
      return;
    }
    if (checkoutPollExhausted) {
      setAwaitingCheckout(false);
      invalidateWorkspaces();
      toast.success(t("toasts.subscriptionProcessing"));
    }
  }, [awaitingCheckout, polledDetail, checkoutPollExhausted, queryClient, t]);

  /** The server's reason in the reader's language where it has a code we know, else its own text. */
  const serverMessage = (payload: { error?: string; code?: string }, fallback: string) => {
    const known: Record<string, string> = {
      seat_limit_reached: t("errors.seatLimitReached"),
      subscription_required: t("errors.subscriptionRequired"),
      invite_rate_limited: t("errors.inviteRateLimited"),
      payment_failed: t("errors.seatPaymentFailed"),
      team_limit_reached: t("errors.teamLimitReached"),
      teams_unavailable: t("errors.teamsUnavailable"),
    };
    return (payload.code && known[payload.code]) || payload.error || fallback;
  };

  const handleCreateTeam = async (event: React.FormEvent) => {
    event.preventDefault();
    const trimmed = newTeamName.trim();
    if (trimmed.length < 2) {
      toast.error(t("errors.nameTooShort"));
      return;
    }
    setCreatingTeam(true);
    try {
      await createTeam(trimmed);
      setNewTeamName("");
      window.location.reload();
    } catch (error) {
      toast.error(
        error instanceof WorkspaceRequestError
          ? serverMessage({ error: error.message, code: error.code }, t("errors.createFailed"))
          : (error instanceof Error && error.message) || t("errors.createFailed"),
      );
      setCreatingTeam(false);
    }
  };

  const [switchingWorkspace, setSwitchingWorkspace] = useState(false);
  const openWorkspace = async (teamId: string | null, redirectTo: string) => {
    if (switchingWorkspace) return;
    setSwitchingWorkspace(true);
    try {
      // Navigates away on success.
      await switchWorkspace(teamId, redirectTo);
    } catch (error) {
      toast.error((error instanceof Error && error.message) || t("errors.switchFailed"));
      setSwitchingWorkspace(false);
    }
  };

  const handleRenameTeam = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!detail) return;
    const trimmed = teamNameDraft.trim();
    if (trimmed.length < 2) {
      toast.error(t("errors.nameTooShort"));
      return;
    }
    setSavingName(true);
    try {
      const response = await fetch(`/api/teams/${encodeURIComponent(detail.team.id)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: trimmed }),
      });
      const payload = (await response.json().catch(() => ({}))) as { error?: string };
      if (!response.ok) throw new Error(payload.error || t("errors.updateFailed"));
      toast.success(t("toasts.renamed"));
      invalidateWorkspaces();
      await refreshDetail(detail.team.id);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("errors.updateFailed"));
    } finally {
      setSavingName(false);
    }
  };

  const handleCheckout = async () => {
    if (!detail) return;
    setBillingBusy(true);
    try {
      const response = await fetch(
        `/api/teams/${encodeURIComponent(detail.team.id)}/billing/checkout`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ seats: desiredSeats, interval: billingInterval }),
        },
      );
      const payload = (await response.json().catch(() => ({}))) as {
        url?: string;
        error?: string;
      };
      if (!response.ok || !payload.url) {
        throw new Error(payload.error || t("errors.checkoutFailed"));
      }
      window.location.href = payload.url;
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("errors.checkoutFailed"));
      setBillingBusy(false);
    }
  };

  const handleUpdateSeats = async () => {
    if (!detail) return;
    setBillingBusy(true);
    try {
      const response = await fetch(`/api/teams/${encodeURIComponent(detail.team.id)}/seats`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ seats: desiredSeats }),
      });
      const payload = (await response.json().catch(() => ({}))) as { error?: string; code?: string };
      if (!response.ok) {
        throw new Error(serverMessage(payload, t("errors.seatsUpdateFailed")));
      }
      toast.success(t("toasts.seatsUpdated", { count: desiredSeats }));
      invalidateWorkspaces();
      await refreshDetail(detail.team.id);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("errors.seatsUpdateFailed"));
    } finally {
      setBillingBusy(false);
    }
  };

  const handleOpenPortal = async () => {
    if (!detail) return;
    setBillingBusy(true);
    try {
      const response = await fetch(
        `/api/teams/${encodeURIComponent(detail.team.id)}/billing/portal`,
        { method: "POST" },
      );
      const payload = (await response.json().catch(() => ({}))) as {
        url?: string;
        error?: string;
      };
      if (!response.ok || !payload.url) {
        throw new Error(payload.error || t("errors.portalFailed"));
      }
      window.location.href = payload.url;
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("errors.portalFailed"));
      setBillingBusy(false);
    }
  };

  const handleInvite = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!detail) return;
    const email = inviteEmail.trim();
    if (!email) return;
    setInviting(true);
    try {
      const response = await fetch(
        `/api/teams/${encodeURIComponent(detail.team.id)}/invitations`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email, role: detail.currentUserRole === "owner" ? inviteRole : "member" }),
        },
      );
      const payload = (await response.json().catch(() => ({}))) as {
        inviteUrl?: string;
        emailSent?: boolean;
        error?: string;
        code?: string;
      };
      if (!response.ok) {
        toast.error(serverMessage(payload, t("errors.inviteFailed")));
        return;
      }
      setInviteEmail("");
      setCopiedLink(false);
      if (payload.inviteUrl) {
        setLastInviteLink({
          email,
          url: payload.inviteUrl,
          emailSent: Boolean(payload.emailSent),
        });
      }
      toast.success(
        payload.emailSent ? t("toasts.inviteSent", { email }) : t("toasts.inviteCreated", { email }),
      );
      invalidateWorkspaces();
      await refreshDetail(detail.team.id);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("errors.inviteFailed"));
    } finally {
      setInviting(false);
    }
  };

  const handleResendInvite = async (invite: TeamInvitationItem) => {
    if (!detail) return;
    setBusyInviteId(invite.id);
    try {
      const response = await fetch(
        `/api/teams/${encodeURIComponent(detail.team.id)}/invitations/${encodeURIComponent(invite.id)}`,
        { method: "POST" },
      );
      const payload = (await response.json().catch(() => ({}))) as {
        inviteUrl?: string;
        emailSent?: boolean;
        error?: string;
        code?: string;
      };
      if (!response.ok) throw new Error(serverMessage(payload, t("errors.resendFailed")));
      if (payload.inviteUrl) {
        setCopiedLink(false);
        setLastInviteLink({
          email: invite.email,
          url: payload.inviteUrl,
          emailSent: Boolean(payload.emailSent),
        });
      }
      toast.success(t("toasts.inviteResent", { email: invite.email }));
      await refreshDetail(detail.team.id);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("errors.resendFailed"));
    } finally {
      setBusyInviteId(null);
    }
  };

  const handleRevokeInvite = async (invite: TeamInvitationItem) => {
    if (!detail) return;
    setBusyInviteId(invite.id);
    try {
      const response = await fetch(
        `/api/teams/${encodeURIComponent(detail.team.id)}/invitations/${encodeURIComponent(invite.id)}`,
        { method: "DELETE" },
      );
      const payload = (await response.json().catch(() => ({}))) as { error?: string };
      if (!response.ok) throw new Error(payload.error || t("errors.revokeFailed"));
      toast.success(t("toasts.inviteRevoked", { email: invite.email }));
      invalidateWorkspaces();
      await refreshDetail(detail.team.id);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("errors.revokeFailed"));
    } finally {
      setBusyInviteId(null);
    }
  };

  const handleRoleChange = async (member: TeamMemberItem, role: "admin" | "member") => {
    if (!detail || member.role === role) return;
    setBusyMemberId(member.userId);
    try {
      const response = await fetch(
        `/api/teams/${encodeURIComponent(detail.team.id)}/members/${encodeURIComponent(member.userId)}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ role }),
        },
      );
      const payload = (await response.json().catch(() => ({}))) as { error?: string };
      if (!response.ok) throw new Error(payload.error || t("errors.roleUpdateFailed"));
      toast.success(t("toasts.roleUpdated"));
      await refreshDetail(detail.team.id);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("errors.roleUpdateFailed"));
    } finally {
      setBusyMemberId(null);
    }
  };

  const openLimitsDialog = (member: TeamMemberItem) => {
    setLimitsTarget(member);
    setMinuteLimitDraft(
      member.monthlyMinuteLimit !== null && member.monthlyMinuteLimit !== undefined
        ? String(member.monthlyMinuteLimit)
        : "",
    );
    setAiLimitDraft(
      member.monthlyAiEditLimit !== null && member.monthlyAiEditLimit !== undefined
        ? String(member.monthlyAiEditLimit)
        : "",
    );
  };

  const handleSaveLimits = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!detail || !limitsTarget) return;

    const parseOptionalLimit = (raw: string): number | null => {
      const trimmed = raw.trim();
      if (!trimmed) return null;
      const parsed = Number(trimmed);
      if (!Number.isInteger(parsed) || parsed < 0) return Number.NaN;
      return parsed;
    };

    const monthlyMinuteLimit = parseOptionalLimit(minuteLimitDraft);
    const monthlyAiEditLimit = parseOptionalLimit(aiLimitDraft);
    if (Number.isNaN(monthlyMinuteLimit) || Number.isNaN(monthlyAiEditLimit)) {
      toast.error(t("errors.limitsUpdateFailed"));
      return;
    }

    setSavingLimits(true);
    try {
      const response = await fetch(
        `/api/teams/${encodeURIComponent(detail.team.id)}/members/${encodeURIComponent(limitsTarget.userId)}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ monthlyMinuteLimit, monthlyAiEditLimit }),
        },
      );
      const payload = (await response.json().catch(() => ({}))) as { error?: string };
      if (!response.ok) throw new Error(payload.error || t("errors.limitsUpdateFailed"));
      toast.success(t("toasts.limitsUpdated"));
      setLimitsTarget(null);
      await refreshDetail(detail.team.id);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("errors.limitsUpdateFailed"));
    } finally {
      setSavingLimits(false);
    }
  };

  const handleRemoveMember = async (member: TeamMemberItem) => {
    if (!detail) return;
    const isSelf = member.userId === session?.user?.id;
    const confirmed = window.confirm(
      isSelf
        ? t("members.leaveConfirm", { team: detail.team.name })
        : t("members.removeConfirm", { name: member.name || member.email }),
    );
    if (!confirmed) return;

    setBusyMemberId(member.userId);
    try {
      const response = await fetch(
        `/api/teams/${encodeURIComponent(detail.team.id)}/members/${encodeURIComponent(member.userId)}`,
        { method: "DELETE" },
      );
      const payload = (await response.json().catch(() => ({}))) as { error?: string };
      if (!response.ok) throw new Error(payload.error || t("errors.removeMemberFailed"));
      invalidateWorkspaces();
      if (isSelf) {
        announceWorkspaceSwitch();
        toast.success(t("toasts.leftTeam"));
        window.location.assign("/settings/team");
        return;
      }
      toast.success(t("toasts.memberRemoved"));
      await refreshDetail(detail.team.id);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("errors.removeMemberFailed"));
    } finally {
      setBusyMemberId(null);
    }
  };

  const handleTransferOwnership = async () => {
    if (!detail || !transferTargetId) return;
    const target = detail.members.find((m) => m.userId === transferTargetId);
    if (!target) return;
    if (
      !window.confirm(
        t("danger.transferConfirm", {
          name: target.name || target.email,
          team: detail.team.name,
        }),
      )
    ) {
      return;
    }
    setTransferring(true);
    try {
      const response = await fetch(`/api/teams/${encodeURIComponent(detail.team.id)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ transferOwnerId: transferTargetId }),
      });
      const payload = (await response.json().catch(() => ({}))) as { error?: string };
      if (!response.ok) throw new Error(payload.error || t("errors.transferFailed"));
      toast.success(t("toasts.ownershipTransferred"));
      setTransferTargetId("");
      invalidateWorkspaces();
      await refreshDetail(detail.team.id);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("errors.transferFailed"));
    } finally {
      setTransferring(false);
    }
  };

  const handleDeleteTeam = async () => {
    if (!detail) return;
    setDeletingTeam(true);
    try {
      const response = await fetch(`/api/teams/${encodeURIComponent(detail.team.id)}`, {
        method: "DELETE",
      });
      const payload = (await response.json().catch(() => ({}))) as { error?: string };
      if (!response.ok) throw new Error(payload.error || t("errors.deleteFailed"));
      invalidateWorkspaces();
      announceWorkspaceSwitch();
      toast.success(t("toasts.teamDeleted"));
      window.location.assign("/settings/team");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("errors.deleteFailed"));
      setDeletingTeam(false);
    }
  };

  const copyInviteUrl = async (url: string) => {
    try {
      await navigator.clipboard.writeText(url);
      setCopiedLink(true);
      toast.success(common("copy.copied"));
    } catch {
      toast.error(common("copy.manual"));
    }
  };

  if (isPending || (session?.user && (workspaceLoading || (activeTeamId && loadingDetail && !detail)))) {
    return (
      <div role="status" aria-label={common("loading")} className="mx-auto w-full max-w-3xl space-y-6 px-4 py-8 sm:px-8 md:py-10">
        <Skeleton className="h-9 w-48" />
        {[0, 1, 2].map((key) => (
          <Skeleton key={key} className="h-44 w-full rounded-2xl" />
        ))}
      </div>
    );
  }

  if (!session?.user) {
    return (
      <div className="mx-auto max-w-md px-4 py-24 text-center">
        <h1 className="font-display text-3xl font-bold tracking-tight">{t("signInRequired.title")}</h1>
        <p className="mb-8 mt-3 text-muted-foreground">{t("signInRequired.body")}</p>
        <Button asChild size="lg">
          <Link href="/sign-in?next=/settings/team">{t("signInRequired.action")}</Link>
        </Button>
      </div>
    );
  }

  // Personal workspace view: switch to an existing team or create a new one.
  if (!activeTeamId) {
    const preview = calculateTeamPricing(teamConfig.includedSeats, billingInterval, teamConfig);
    return (
      <main className="mx-auto w-full max-w-3xl px-4 pb-16 pt-8 sm:px-8 md:pt-10">
        <Link
          href="/settings"
          className="mb-4 inline-flex items-center gap-1.5 text-xs font-medium text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="size-3.5" />
          {t("backToSettings")}
        </Link>
        <h1 className="font-display text-3xl font-bold tracking-tight">{t("title")}</h1>
        <p className="mt-1 text-sm text-muted-foreground">{t("personalIntro")}</p>

        <div className="mt-8 space-y-6">
          {teams.length > 0 && (
            <section className="rounded-2xl border bg-background">
              <div className="flex items-start gap-3 border-b px-5 py-4">
                <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg bg-muted">
                  <Users className="size-4" />
                </span>
                <div>
                  <h2 className="text-sm font-semibold">{t("yourTeams.title")}</h2>
                  <p className="text-xs text-muted-foreground">{t("yourTeams.description")}</p>
                </div>
              </div>
              <div className="divide-y p-2">
                {teams.map((team) => (
                  <div
                    key={team.id}
                    className="flex flex-wrap items-center justify-between gap-3 rounded-xl px-3 py-3"
                  >
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <p className="truncate text-sm font-semibold">{team.name}</p>
                        <Badge variant="secondary" className="text-[11px]">
                          {t(`roles.${team.role}`)}
                        </Badge>
                      </div>
                      <p className="mt-0.5 text-xs text-muted-foreground">
                        {t("seats.shortCount", {
                          occupied: team.occupiedSeats,
                          limit: team.seatLimit,
                        })}
                      </p>
                    </div>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={switchingWorkspace}
                      onClick={() => void openWorkspace(team.id, "/settings/team")}
                    >
                      {t("yourTeams.open")}
                      <ArrowRight className="size-3.5" />
                    </Button>
                  </div>
                ))}
              </div>
            </section>
          )}

          {!teamsAvailable ? (
            <p className="rounded-2xl border bg-background px-5 py-4 text-sm text-muted-foreground">
              {t("create.unavailable")}
            </p>
          ) : (
          <section className="rounded-2xl border bg-background">
            <div className="flex items-start gap-3 border-b px-5 py-4">
              <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg bg-brand/15 text-brand">
                <Plus className="size-4" />
              </span>
              <div>
                <h2 className="text-sm font-semibold">{t("create.cardTitle")}</h2>
                <p className="text-xs text-muted-foreground">{t("create.cardDescription")}</p>
              </div>
            </div>
            <div className="space-y-5 p-5">
              <div className="rounded-xl border bg-muted/40 p-4">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <p className="text-sm font-semibold">{common("plans.team")}</p>
                  <p className="text-sm font-semibold tabular-nums">
                    ${formatCurrencyAmount(preview.basePrice)}
                    <span className="text-xs font-normal text-muted-foreground">
                      {billingInterval === "year" ? t("billing.perYear") : t("billing.perMonth")}
                    </span>
                  </p>
                </div>
                <p className="mt-1 text-xs text-muted-foreground">
                  {t("create.planSummary", {
                    seats: teamConfig.includedSeats,
                    hours: Math.round(teamConfig.baseMinuteLimit / 60),
                    extraPrice: teamConfig.seatPriceMonthly,
                    extraHours: Math.round(teamConfig.extraSeatMinuteLimit / 60),
                  })}
                </p>
              </div>

              <form onSubmit={handleCreateTeam} className="flex flex-col gap-3 sm:flex-row sm:items-end">
                <div className="flex-1 space-y-1.5">
                  <Label htmlFor="personal-create-team-name">{t("create.nameLabel")}</Label>
                  <Input
                    id="personal-create-team-name"
                    value={newTeamName}
                    onChange={(event) => setNewTeamName(event.target.value)}
                    placeholder={t("create.namePlaceholder")}
                    maxLength={80}
                    required
                    disabled={creatingTeam}
                  />
                </div>
                <Button type="submit" disabled={creatingTeam || newTeamName.trim().length < 2}>
                  {creatingTeam && <Loader2 className="size-4 animate-spin" />}
                  {creatingTeam ? t("create.creating") : t("create.submit")}
                </Button>
              </form>
            </div>
          </section>
          )}
        </div>
      </main>
    );
  }

  if (detailError || !detail) {
    return (
      <main className="mx-auto w-full max-w-3xl px-4 py-12 sm:px-8">
        <Alert className="border-red-200 bg-red-50 dark:border-red-900/60 dark:bg-red-950/40">
          <AlertCircle className="size-4 text-red-500" />
          <AlertDescription className="text-sm text-red-700 dark:text-red-300">
            {detailError || t("errors.loadFailed")}
          </AlertDescription>
        </Alert>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="mt-4"
          disabled={loadingDetail}
          onClick={retryDetail}
        >
          {loadingDetail && <Loader2 className="size-3.5 animate-spin" />}
          {t("invite.retry")}
        </Button>
      </main>
    );
  }

  const isSubscribed = hasPaidAccess(
    detail.team.plan,
    detail.team.subscriptionStatus,
    detail.team.subscriptionProvider,
  );
  const paymentNeedsUpdate =
    monetizationEnabled &&
    (detail.team.subscriptionStatus === "past_due" || detail.team.subscriptionStatus === "unpaid");

  const billingUsage = detail.usage?.billing;
  const aiUsage = detail.usage?.ai_edits;
  const usedMinutes = billingUsage?.usage_minutes ?? 0;
  const limitMinutes = billingUsage?.minute_limit ?? null;
  const remainingMinutes =
    billingUsage?.remaining_minutes ??
    (limitMinutes !== null ? Math.max(limitMinutes - usedMinutes, 0) : null);
  const minutesPct = limitMinutes ? Math.min(100, (usedMinutes / limitMinutes) * 100) : 0;

  const usedAiEdits = aiUsage?.used ?? 0;
  const limitAiEdits = aiUsage?.limit ?? null;
  const aiPct = limitAiEdits ? Math.min(100, (usedAiEdits / limitAiEdits) * 100) : 0;

  const seatPct = detail.seats.limit
    ? Math.min(100, (detail.seats.occupied / detail.seats.limit) * 100)
    : 0;
  const atSeatCap = detail.seats.occupied >= detail.seats.limit;
  // On a hosted install invitations are sent by subscribed teams only.
  const needsPlanToInvite = monetizationEnabled && !isSubscribed;
  const minAllowedSeats = isSubscribed
    ? Math.max(teamConfig.includedSeats, detail.seats.occupied)
    : teamConfig.includedSeats;
  const clampedDesiredSeats = Math.min(MAX_TEAM_SEATS, Math.max(minAllowedSeats, desiredSeats));
  const pricing = calculateTeamPricing(clampedDesiredSeats, billingInterval, teamConfig);
  const seatsChanged = clampedDesiredSeats !== detail.seats.limit;

  const eligibleTransferTargets = detail.members.filter(
    (member) => member.userId !== detail.team.ownerId,
  );

  return (
    <main className="mx-auto w-full max-w-3xl px-4 pb-20 pt-8 sm:px-8 md:pt-10">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <Link
            href="/settings"
            className="mb-3 inline-flex items-center gap-1.5 text-xs font-medium text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="size-3.5" />
            {t("backToSettings")}
          </Link>
          <div className="flex flex-wrap items-center gap-2.5">
            <h1 className="font-display text-3xl font-bold tracking-tight">{detail.team.name}</h1>
            <Badge variant="secondary">{t(`roles.${detail.currentUserRole}`)}</Badge>
            {monetizationEnabled && (
              <Badge variant={isSubscribed ? "default" : "outline"} className="capitalize">
                {isSubscribed ? common("plans.team") : detail.team.subscriptionStatus}
              </Badge>
            )}
          </div>
          <p className="mt-1 text-sm text-muted-foreground">{t("teamIntro")}</p>
        </div>

        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={switchingWorkspace}
          onClick={() => void openWorkspace(null, "/settings")}
        >
          {t("switchToPersonal")}
        </Button>
      </div>

      <div className="mt-8 space-y-6">
        {paymentNeedsUpdate && (
          <Alert className="border-amber-300 bg-amber-50 dark:border-amber-900/60 dark:bg-amber-950/40">
            <AlertCircle className="size-4 text-amber-600" />
            <AlertDescription className="flex flex-wrap items-center justify-between gap-3 text-sm text-amber-900 dark:text-amber-200">
              <span>{t("billing.paymentFailed")}</span>
              {detail.permissions.canManageBilling && detail.team.hasStripeCustomer && (
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => void handleOpenPortal()}
                  disabled={billingBusy}
                >
                  {t("billing.updatePayment")}
                </Button>
              )}
            </AlertDescription>
          </Alert>
        )}

        {detail.team.cancelAt && (
          <Alert>
            <AlertCircle className="size-4" />
            <AlertDescription className="text-sm">
              {t("billing.cancelScheduled", {
                date: formatShortDate(detail.team.cancelAt) ?? detail.team.cancelAt,
              })}
            </AlertDescription>
          </Alert>
        )}

        {/* Overview metrics */}
        <section className="grid gap-4 sm:grid-cols-3">
          <div className="rounded-2xl border bg-background p-4">
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span>{t("overview.pooledVideo")}</span>
              <span className="tabular-nums font-medium text-foreground">
                {limitMinutes !== null
                  ? t("overview.minutesOf", { used: usedMinutes, limit: limitMinutes })
                  : t("overview.minutesUsed", { used: usedMinutes })}
              </span>
            </div>
            {limitMinutes !== null && (
              <div className="mt-2.5 h-1.5 overflow-hidden rounded-full bg-muted">
                <div className="h-full rounded-full bg-brand" style={{ width: `${minutesPct}%` }} />
              </div>
            )}
            <p className="mt-2 text-xs text-muted-foreground">
              {remainingMinutes !== null
                ? t("overview.minutesRemaining", { remaining: remainingMinutes })
                : billingUsage
                  ? t("overview.unlimited")
                  : null /* usage did not load: that is not "unlimited" */}
            </p>
            {billingUsage?.member_minute_limit !== null &&
              billingUsage?.member_minute_limit !== undefined && (
                <p className="mt-1 text-[11px] font-medium text-amber-700 dark:text-amber-300">
                  {billingUsage.member_minute_limit === 0
                    ? t("overview.yourLimitPaused")
                    : t("overview.yourLimitMinutes", {
                        used: billingUsage.member_usage_minutes ?? 0,
                        limit: billingUsage.member_minute_limit,
                      })}
                </p>
              )}
          </div>

          <div className="rounded-2xl border bg-background p-4">
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span>{t("overview.aiEdits")}</span>
              <span className="tabular-nums font-medium text-foreground">
                {limitAiEdits !== null
                  ? t("overview.editsOf", { used: usedAiEdits, limit: limitAiEdits })
                  : usedAiEdits}
              </span>
            </div>
            {limitAiEdits !== null && (
              <div className="mt-2.5 h-1.5 overflow-hidden rounded-full bg-muted">
                <div className="h-full rounded-full bg-brand" style={{ width: `${aiPct}%` }} />
              </div>
            )}
            <p className="mt-2 text-xs text-muted-foreground">{t("overview.pooledMonthly")}</p>
            {aiUsage?.memberLimit !== null && aiUsage?.memberLimit !== undefined && (
              <p className="mt-1 text-[11px] font-medium text-amber-700 dark:text-amber-300">
                {t("overview.yourLimitAiEdits", {
                  used: aiUsage.memberUsed ?? 0,
                  limit: aiUsage.memberLimit,
                })}
              </p>
            )}
          </div>

          <div className="rounded-2xl border bg-background p-4">
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span>{t("overview.seats")}</span>
              <span className="tabular-nums font-medium text-foreground">
                {t("seats.shortCount", {
                  occupied: detail.seats.occupied,
                  limit: detail.seats.limit,
                })}
              </span>
            </div>
            <div className="mt-2.5 h-1.5 overflow-hidden rounded-full bg-muted">
              <div
                className={cn(
                  "h-full rounded-full",
                  atSeatCap ? "bg-amber-500" : "bg-brand",
                )}
                style={{ width: `${seatPct}%` }}
              />
            </div>
            <p className="mt-2 text-xs text-muted-foreground">
              {t("seats.breakdown", {
                members: detail.seats.activeMembers,
                pending: detail.seats.pendingInvitations,
              })}
            </p>
          </div>
        </section>

        {/* Team Plan & Manage Seats */}
        <section id="seats" className="scroll-mt-6 rounded-2xl border bg-background">
          <div className="flex items-start gap-3 border-b px-5 py-4">
            <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg bg-muted">
              <CreditCard className="size-4" />
            </span>
            <div className="min-w-0 flex-1">
              <h2 className="text-sm font-semibold">{t("billing.title")}</h2>
              <p className="text-xs text-muted-foreground">
                {isSubscribed ? t("billing.activeDescription") : t("billing.inactiveDescription")}
              </p>
            </div>
          </div>

          <div className="space-y-5 p-5">
            {monetizationEnabled && !isSubscribed && (
              <BillingIntervalToggle
                value={billingInterval}
                onChange={setBillingInterval}
                plan={getPublicBillingPlans()[0]}
              />
            )}

            <div className="flex flex-col gap-4 rounded-xl border bg-muted/30 p-4 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <p className="text-sm font-semibold">{t("seats.stepperTitle")}</p>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  {t("seats.stepperHint", {
                    included: teamConfig.includedSeats,
                    extraHours: Math.round(teamConfig.extraSeatMinuteLimit / 60),
                    extraPrice:
                      billingInterval === "year"
                        ? teamConfig.seatPriceAnnual
                        : teamConfig.seatPriceMonthly,
                    period: billingInterval === "year" ? t("billing.perYear") : t("billing.perMonth"),
                  })}
                </p>
              </div>

              <div className="flex items-center gap-2">
                <Button
                  type="button"
                  variant="outline"
                  size="icon"
                  disabled={
                    billingBusy ||
                    !detail.permissions.canManageBilling ||
                    clampedDesiredSeats <= minAllowedSeats
                  }
                  onClick={() => setDesiredSeats((value) => Math.max(minAllowedSeats, value - 1))}
                  aria-label={t("seats.decrease")}
                >
                  <Minus className="size-4" />
                </Button>
                <span className="w-12 text-center font-display text-xl font-bold tabular-nums">
                  {clampedDesiredSeats}
                </span>
                <Button
                  type="button"
                  variant="outline"
                  size="icon"
                  disabled={
                    billingBusy ||
                    !detail.permissions.canManageBilling ||
                    clampedDesiredSeats >= MAX_TEAM_SEATS
                  }
                  onClick={() => setDesiredSeats((value) => Math.min(MAX_TEAM_SEATS, value + 1))}
                  aria-label={t("seats.increase")}
                >
                  <Plus className="size-4" />
                </Button>
              </div>
            </div>

            {isSubscribed && detail.seats.occupied > teamConfig.includedSeats && (
              <p className="text-xs text-muted-foreground">
                {t("seats.minimumNote", { occupied: detail.seats.occupied })}
              </p>
            )}

            {/* Live pricing + capacity breakdown */}
            <div className="space-y-2 rounded-xl border p-4 text-sm">
              <div className="flex items-center justify-between gap-2 text-muted-foreground">
                <span>
                  {t("billing.baseLine", {
                    seats: pricing.includedSeats,
                    hours: Math.round(teamConfig.baseMinuteLimit / 60),
                  })}
                </span>
                <span className="tabular-nums">
                  ${formatCurrencyAmount(pricing.basePrice)}
                  {billingInterval === "year" ? t("billing.perYear") : t("billing.perMonth")}
                </span>
              </div>
              {pricing.extraSeats > 0 && (
                <div className="flex items-center justify-between gap-2 text-muted-foreground">
                  <span>
                    {t("billing.extraSeatsLine", {
                      count: pricing.extraSeats,
                      price: formatCurrencyAmount(pricing.seatPrice),
                      hours: Math.round(
                        (pricing.extraSeats * teamConfig.extraSeatMinuteLimit) / 60,
                      ),
                    })}
                  </span>
                  <span className="tabular-nums">
                    +${formatCurrencyAmount(pricing.extraSeatsSubtotal)}
                    {billingInterval === "year" ? t("billing.perYear") : t("billing.perMonth")}
                  </span>
                </div>
              )}
              <div className="flex items-baseline justify-between gap-2 border-t pt-2.5 font-semibold">
                <span>
                  {t("billing.totalLine", {
                    seats: pricing.seats,
                    hours: pricing.totalHours,
                  })}
                </span>
                <span className="text-base tabular-nums">
                  ${formatCurrencyAmount(pricing.totalPrice)}
                  <span className="text-xs font-normal text-muted-foreground">
                    {billingInterval === "year" ? t("billing.perYear") : t("billing.perMonth")}
                  </span>
                </span>
              </div>
              <p className="text-xs text-muted-foreground">
                {t(socialPublishingStage() === "off" ? "billing.includedFeaturesNoScheduling" : "billing.includedFeatures", {
                  resolution: teamConfig.resolution,
                  youtubeHours: Math.round(teamConfig.youtubeMaxMinutes / 60),
                  aiEdits: pricing.aiEditLimit,
                })}
              </p>
            </div>

            {detail.permissions.canManageBilling ? (
              <div className="flex flex-wrap items-center gap-3">
                {monetizationEnabled && !isSubscribed ? (
                  <Button type="button" onClick={() => void handleCheckout()} disabled={billingBusy}>
                    {billingBusy ? (
                      <Loader2 className="size-4 animate-spin" />
                    ) : (
                      <Sparkles className="size-4" />
                    )}
                    {billingBusy ? t("billing.openingCheckout") : t("billing.startPlan")}
                  </Button>
                ) : (
                  <>
                    <Button
                      type="button"
                      onClick={() => void handleUpdateSeats()}
                      disabled={billingBusy || !seatsChanged}
                    >
                      {billingBusy && <Loader2 className="size-4 animate-spin" />}
                      {t("seats.saveButton", { count: clampedDesiredSeats })}
                    </Button>
                    {monetizationEnabled && detail.team.hasStripeCustomer && (
                      <Button
                        type="button"
                        variant="outline"
                        onClick={() => void handleOpenPortal()}
                        disabled={billingBusy}
                      >
                        {t("billing.managePortal")}
                        <ExternalLink className="size-3.5" />
                      </Button>
                    )}
                  </>
                )}
                {isSubscribed && monetizationEnabled && seatsChanged && (
                  <span className="text-xs text-muted-foreground">{t("billing.prorationNote")}</span>
                )}
              </div>
            ) : (
              <p className="rounded-lg border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
                {t("billing.ownerOnlyNotice")}
              </p>
            )}
          </div>
        </section>

        {/* Members & Pending Invitations */}
        <section className="rounded-2xl border bg-background">
          <div className="flex items-start gap-3 border-b px-5 py-4">
            <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg bg-muted">
              <Users className="size-4" />
            </span>
            <div className="min-w-0 flex-1">
              <h2 className="text-sm font-semibold">{t("members.title")}</h2>
              <p className="text-xs text-muted-foreground">
                {t("seats.usedSummary", {
                  occupied: detail.seats.occupied,
                  limit: detail.seats.limit,
                })}
              </p>
            </div>
          </div>

          <div className="space-y-6 p-5">
            {detail.permissions.canManageMembers && (
              <div className="space-y-3">
                {needsPlanToInvite && (
                  <Alert>
                    <AlertCircle className="size-4" />
                    <AlertDescription className="text-xs">{t("members.subscribeToInvite")}</AlertDescription>
                  </Alert>
                )}

                {atSeatCap && !needsPlanToInvite && (
                  <Alert className="border-amber-300 bg-amber-50 dark:border-amber-900/60 dark:bg-amber-950/40">
                    <AlertCircle className="size-4 text-amber-600" />
                    <AlertDescription className="flex flex-wrap items-center justify-between gap-2 text-xs text-amber-900 dark:text-amber-200">
                      <span>
                        {t("seats.capReachedBanner", {
                          occupied: detail.seats.occupied,
                          limit: detail.seats.limit,
                        })}
                      </span>
                      <a
                        href="#seats"
                        className="font-semibold underline underline-offset-4 hover:opacity-80"
                      >
                        {t("seats.addMoreSeats")}
                      </a>
                    </AlertDescription>
                  </Alert>
                )}

                <form
                  onSubmit={handleInvite}
                  className="flex flex-col gap-2.5 sm:flex-row sm:items-end"
                >
                  <div className="flex flex-1 flex-col gap-1.5">
                    <Label htmlFor="invite-member-email">{t("members.inviteEmailLabel")}</Label>
                    <Input
                      id="invite-member-email"
                      type="email"
                      value={inviteEmail}
                      onChange={(event) => setInviteEmail(event.target.value)}
                      placeholder={t("members.inviteEmailPlaceholder")}
                      required
                      disabled={inviting || atSeatCap || needsPlanToInvite}
                    />
                  </div>
                  {/* Admins invite members; only the owner can add another admin. */}
                  {detail.currentUserRole === "owner" && (
                    <div className="flex w-full flex-col gap-1.5 sm:w-36">
                      <Label htmlFor="invite-member-role">{t("members.roleLabel")}</Label>
                      <Select
                        value={inviteRole}
                        onValueChange={(value) => setInviteRole(value as "member" | "admin")}
                        disabled={inviting || atSeatCap || needsPlanToInvite}
                      >
                        <SelectTrigger id="invite-member-role" className="w-full">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="member">{t("roles.member")}</SelectItem>
                          <SelectItem value="admin">{t("roles.admin")}</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                  )}
                  <Button type="submit" disabled={inviting || atSeatCap || needsPlanToInvite || !inviteEmail.trim()}>
                    {inviting ? (
                      <Loader2 className="size-4 animate-spin" />
                    ) : (
                      <UserPlus className="size-4" />
                    )}
                    {inviting ? t("members.inviting") : t("members.inviteButton")}
                  </Button>
                </form>

                {lastInviteLink && (
                  <div className="rounded-xl border bg-muted/40 p-3.5 text-xs">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <p className="font-medium text-foreground">
                        {lastInviteLink.emailSent
                          ? t("members.inviteLinkSent", { email: lastInviteLink.email })
                          : t("members.inviteLinkShare", { email: lastInviteLink.email })}
                      </p>
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        className="h-7 px-2.5 text-xs"
                        onClick={() => void copyInviteUrl(lastInviteLink.url)}
                      >
                        {copiedLink ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
                        {copiedLink ? t("actions.copied") : t("members.copyInviteLink")}
                      </Button>
                    </div>
                    <p className="mt-1.5 truncate font-mono text-[11px] text-muted-foreground">
                      {lastInviteLink.url}
                    </p>
                  </div>
                )}
              </div>
            )}

            {/* Active members */}
            <div className="space-y-2">
              <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                {t("members.activeHeading", { count: detail.members.length })}
              </h3>
              <div className="divide-y rounded-xl border">
                {detail.members.map((member) => {
                  const isSelf = member.userId === session.user.id;
                  const canEditRole =
                    detail.currentUserRole === "owner" && member.role !== "owner" && !isSelf;
                  const canEditLimits =
                    (detail.currentUserRole === "owner" && member.role !== "owner") ||
                    (detail.currentUserRole === "admin" && member.role === "member");
                  const canRemove =
                    (isSelf && member.role !== "owner") ||
                    (detail.currentUserRole === "owner" && member.role !== "owner") ||
                    (detail.currentUserRole === "admin" && member.role === "member");
                  const busy = busyMemberId === member.userId;
                  const hasMinuteCap =
                    member.monthlyMinuteLimit !== null && member.monthlyMinuteLimit !== undefined;
                  const hasAiCap =
                    member.monthlyAiEditLimit !== null && member.monthlyAiEditLimit !== undefined;
                  const hasCustomLimits = hasMinuteCap || hasAiCap;
                  const memberMinutesUsed = member.usageMinutes ?? 0;
                  const memberAiUsed = member.usageAiEdits ?? 0;
                  const minuteCapHit =
                    hasMinuteCap &&
                    (member.monthlyMinuteLimit === 0 ||
                      memberMinutesUsed >= (member.monthlyMinuteLimit ?? 0));
                  const aiCapHit =
                    hasAiCap &&
                    (member.monthlyAiEditLimit === 0 ||
                      memberAiUsed >= (member.monthlyAiEditLimit ?? 0));
                  const showUsageRow =
                    detail.permissions.canManageMembers || isSelf;

                  return (
                    <div
                      key={member.userId}
                      className="flex flex-wrap items-center justify-between gap-3 px-4 py-3"
                    >
                      <div className="flex min-w-0 items-center gap-3">
                        <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-semibold">
                          {memberInitials(member.name, member.email)}
                        </span>
                        <div className="min-w-0">
                          <p className="truncate text-sm font-medium">
                            {member.name || member.email}
                            {isSelf && (
                              <span className="ml-1.5 text-xs font-normal text-muted-foreground">
                                ({t("members.you")})
                              </span>
                            )}
                          </p>
                          <p className="truncate text-xs text-muted-foreground">{member.email}</p>
                          {showUsageRow && (hasCustomLimits || memberMinutesUsed > 0 || memberAiUsed > 0) && (
                            <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] tabular-nums">
                              {hasMinuteCap ? (
                                <span
                                  className={cn(
                                    "inline-flex items-center rounded-md border px-1.5 py-0.5 font-medium",
                                    minuteCapHit
                                      ? "border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-900/60 dark:bg-amber-950/40 dark:text-amber-300"
                                      : "border-border bg-muted/50 text-foreground",
                                  )}
                                >
                                  {member.monthlyMinuteLimit === 0
                                    ? t("members.usageMinutesPaused")
                                    : t("members.usageMinutesCap", {
                                        used: memberMinutesUsed,
                                        limit: member.monthlyMinuteLimit ?? 0,
                                      })}
                                </span>
                              ) : memberMinutesUsed > 0 ? (
                                <span className="text-muted-foreground">
                                  {t("members.usageMinutesOnly", { used: memberMinutesUsed })}
                                </span>
                              ) : null}

                              {hasAiCap ? (
                                <span
                                  className={cn(
                                    "inline-flex items-center rounded-md border px-1.5 py-0.5 font-medium",
                                    aiCapHit
                                      ? "border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-900/60 dark:bg-amber-950/40 dark:text-amber-300"
                                      : "border-border bg-muted/50 text-foreground",
                                  )}
                                >
                                  {member.monthlyAiEditLimit === 0
                                    ? t("members.usageAiPaused")
                                    : t("members.usageAiCap", {
                                        used: memberAiUsed,
                                        limit: member.monthlyAiEditLimit ?? 0,
                                      })}
                                </span>
                              ) : memberAiUsed > 0 ? (
                                <span className="text-muted-foreground">
                                  {t("members.usageAiOnly", { used: memberAiUsed })}
                                </span>
                              ) : null}
                            </div>
                          )}
                        </div>
                      </div>

                      <div className="flex items-center gap-2">
                        {canEditLimits && (
                          <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            disabled={busy}
                            onClick={() => openLimitsDialog(member)}
                            className="h-8 gap-1.5 px-2.5 text-xs"
                          >
                            <SlidersHorizontal className="size-3.5" />
                            <span>{t("members.limitsButton")}</span>
                            {hasCustomLimits && (
                              <span
                                className="size-1.5 rounded-full bg-brand"
                                aria-hidden="true"
                              />
                            )}
                          </Button>
                        )}

                        {canEditRole ? (
                          <Select
                            value={member.role}
                            onValueChange={(value) =>
                              void handleRoleChange(member, value as "admin" | "member")
                            }
                            disabled={busy}
                          >
                            <SelectTrigger
                              className="h-8 w-28 text-xs"
                              aria-label={t("members.roleFor", { name: member.name || member.email })}
                            >
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="member">{t("roles.member")}</SelectItem>
                              <SelectItem value="admin">{t("roles.admin")}</SelectItem>
                            </SelectContent>
                          </Select>
                        ) : (
                          <Badge variant={member.role === "owner" ? "default" : "secondary"} className="gap-1">
                            {member.role === "owner" && <Crown className="size-3" />}
                            {member.role === "admin" && <Shield className="size-3" />}
                            {t(`roles.${member.role}`)}
                          </Badge>
                        )}

                        {canRemove && (
                          <Button
                            type="button"
                            size="sm"
                            variant="ghost"
                            disabled={busy}
                            onClick={() => void handleRemoveMember(member)}
                            className="h-8 px-2 text-xs text-muted-foreground hover:text-destructive"
                          >
                            {busy ? (
                              <Loader2 className="size-3.5 animate-spin" />
                            ) : (
                              <UserMinus className="size-3.5" />
                            )}
                            <span>{isSelf ? t("members.leave") : t("members.remove")}</span>
                          </Button>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Pending invitations */}
            {detail.permissions.canManageMembers && (
              <div className="space-y-2">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  {t("members.pendingHeading", { count: detail.invitations.length })}
                </h3>
                {detail.invitations.length === 0 ? (
                  <p className="rounded-xl border border-dashed px-4 py-4 text-center text-xs text-muted-foreground">
                    {t("members.noPending")}
                  </p>
                ) : (
                  <div className="divide-y rounded-xl border">
                    {detail.invitations.map((invite) => {
                      const busy = busyInviteId === invite.id;
                      return (
                        <div
                          key={invite.id}
                          className="flex flex-wrap items-center justify-between gap-3 px-4 py-3"
                        >
                          <div className="flex min-w-0 items-center gap-3">
                            <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground">
                              <Mail className="size-3.5" />
                            </span>
                            <div className="min-w-0">
                              <p className="truncate text-sm font-medium">{invite.email}</p>
                              <p className="text-xs text-muted-foreground">
                                {t("members.expiresOn", {
                                  date: formatShortDate(invite.expiresAt) ?? invite.expiresAt,
                                })}
                              </p>
                            </div>
                          </div>

                          <div className="flex items-center gap-2">
                            <Badge variant="outline">{t(`roles.${invite.role}`)}</Badge>
                            <Button
                              type="button"
                              size="sm"
                              variant="outline"
                              disabled={busy}
                              onClick={() => void handleResendInvite(invite)}
                              className="h-8 px-2.5 text-xs"
                            >
                              {busy ? (
                                <Loader2 className="size-3.5 animate-spin" />
                              ) : (
                                <RefreshCw className="size-3.5" />
                              )}
                              {t("members.resend")}
                            </Button>
                            <Button
                              type="button"
                              size="sm"
                              variant="ghost"
                              disabled={busy}
                              onClick={() => void handleRevokeInvite(invite)}
                              className="h-8 px-2 text-xs text-muted-foreground hover:text-destructive"
                            >
                              {t("members.revoke")}
                            </Button>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            )}
          </div>
        </section>

        {/* Team details (rename) */}
        {detail.permissions.canManageMembers && (
          <section className="rounded-2xl border bg-background">
            <div className="border-b px-5 py-4">
              <h2 className="text-sm font-semibold">{t("general.title")}</h2>
              <p className="text-xs text-muted-foreground">{t("general.description")}</p>
            </div>
            <form
              onSubmit={handleRenameTeam}
              className="flex flex-col gap-3 p-5 sm:flex-row sm:items-end"
            >
              <div className="flex-1 space-y-1.5">
                <Label htmlFor="team-name-input">{t("create.nameLabel")}</Label>
                <Input
                  id="team-name-input"
                  value={teamNameDraft}
                  onChange={(event) => setTeamNameDraft(event.target.value)}
                  maxLength={80}
                  required
                  disabled={savingName}
                />
              </div>
              <Button
                type="submit"
                disabled={
                  savingName ||
                  teamNameDraft.trim().length < 2 ||
                  teamNameDraft.trim() === detail.team.name
                }
              >
                {savingName && <Loader2 className="size-4 animate-spin" />}
                {savingName ? common("saving") : common("save")}
              </Button>
            </form>
          </section>
        )}

        {/* Danger Zone (owner only) */}
        {detail.permissions.canDeleteTeam && (
          <section className="rounded-2xl border border-destructive/30 bg-background">
            <div className="border-b border-destructive/20 px-5 py-4">
              <h2 className="text-sm font-semibold text-destructive">{t("danger.title")}</h2>
              <p className="text-xs text-muted-foreground">{t("danger.description")}</p>
            </div>
            <div className="space-y-5 p-5">
              {eligibleTransferTargets.length > 0 && (
                <div className="flex flex-col gap-3 border-b pb-5 sm:flex-row sm:items-end">
                  <div className="flex-1 space-y-1.5">
                    <Label htmlFor="transfer-owner-select">{t("danger.transferLabel")}</Label>
                    <Select
                      value={transferTargetId}
                      onValueChange={setTransferTargetId}
                      disabled={transferring}
                    >
                      <SelectTrigger id="transfer-owner-select">
                        <SelectValue placeholder={t("danger.transferPlaceholder")} />
                      </SelectTrigger>
                      <SelectContent>
                        {eligibleTransferTargets.map((member) => (
                          <SelectItem key={member.userId} value={member.userId}>
                            {member.name ? `${member.name} (${member.email})` : member.email}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <Button
                    type="button"
                    variant="outline"
                    disabled={!transferTargetId || transferring}
                    onClick={() => void handleTransferOwnership()}
                  >
                    {transferring && <Loader2 className="size-4 animate-spin" />}
                    {t("danger.transferAction")}
                  </Button>
                </div>
              )}

              <div className="flex flex-wrap items-center justify-between gap-4">
                <div>
                  <p className="text-sm font-medium">{t("danger.deleteLabel")}</p>
                  <p className="text-xs text-muted-foreground">{t("danger.deleteHint")}</p>
                </div>
                <Button
                  type="button"
                  variant="destructive"
                  onClick={() => {
                    setDeleteConfirmText("");
                    setDeleteDialogOpen(true);
                  }}
                >
                  <Trash2 className="size-4" />
                  {t("danger.deleteAction")}
                </Button>
              </div>
            </div>
          </section>
        )}
      </div>

      <Dialog
        open={Boolean(limitsTarget)}
        onOpenChange={(open) => {
          if (!open && !savingLimits) setLimitsTarget(null);
        }}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{t("members.limitsDialogTitle")}</DialogTitle>
            <DialogDescription>
              {limitsTarget
                ? t("members.limitsDialogDescription", {
                    name: limitsTarget.name || limitsTarget.email,
                    team: detail.team.name,
                  })
                : ""}
            </DialogDescription>
          </DialogHeader>
          {limitsTarget && (
            <form onSubmit={handleSaveLimits} className="space-y-5">
              <div className="rounded-xl border bg-muted/40 px-3.5 py-2.5 text-xs text-muted-foreground tabular-nums">
                {t("members.limitsCurrentUsage", {
                  minutes: limitsTarget.usageMinutes ?? 0,
                  aiEdits: limitsTarget.usageAiEdits ?? 0,
                })}
              </div>

              <div className="space-y-2">
                <div className="flex items-center justify-between gap-2">
                  <Label htmlFor="member-minute-limit">{t("members.minuteLimitLabel")}</Label>
                  <span className="text-xs text-muted-foreground tabular-nums">
                    {minuteLimitDraft.trim() === ""
                      ? t("members.presetUnlimited")
                      : Number(minuteLimitDraft) === 0
                        ? t("members.usageMinutesPaused")
                        : Number.isFinite(Number(minuteLimitDraft)) && Number(minuteLimitDraft) > 0
                          ? t("members.minuteLimitHoursHint", {
                              hours: Number((Number(minuteLimitDraft) / 60).toFixed(1)),
                            })
                          : ""}
                  </span>
                </div>
                <Input
                  id="member-minute-limit"
                  type="number"
                  min={0}
                  max={100000}
                  step={1}
                  value={minuteLimitDraft}
                  onChange={(event) => setMinuteLimitDraft(event.target.value)}
                  placeholder={t("members.minuteLimitPlaceholder")}
                  disabled={savingLimits}
                />
                <div className="flex flex-wrap gap-1.5">
                  {[
                    { label: t("members.presetUnlimited"), value: "" },
                    ...[300, 600, 1200].map((minutes) => ({
                      label: t("members.presetHours", { hours: minutes / 60, minutes }),
                      value: String(minutes),
                    })),
                    { label: t("members.presetPause"), value: "0" },
                  ].map((preset) => {
                    const active = minuteLimitDraft.trim() === preset.value;
                    return (
                      <button
                        key={preset.label}
                        type="button"
                        disabled={savingLimits}
                        onClick={() => setMinuteLimitDraft(preset.value)}
                        className={cn(
                          "rounded-md border px-2 py-1 text-[11px] font-medium transition-colors",
                          active
                            ? "border-foreground bg-foreground text-background"
                            : "border-border bg-background text-muted-foreground hover:text-foreground",
                        )}
                      >
                        {preset.label}
                      </button>
                    );
                  })}
                </div>
                <p className="text-[11px] text-muted-foreground">
                  {minuteLimitDraft.trim() === "0"
                    ? t("members.minuteLimitPausedHint")
                    : t("members.minuteLimitUnlimitedHint")}
                </p>
              </div>

              <div className="space-y-2">
                <Label htmlFor="member-ai-limit">{t("members.aiLimitLabel")}</Label>
                <Input
                  id="member-ai-limit"
                  type="number"
                  min={0}
                  max={10000}
                  step={1}
                  value={aiLimitDraft}
                  onChange={(event) => setAiLimitDraft(event.target.value)}
                  placeholder={t("members.aiLimitPlaceholder")}
                  disabled={savingLimits}
                />
                <div className="flex flex-wrap gap-1.5">
                  {[
                    { label: t("members.presetUnlimited"), value: "" },
                    { label: "25", value: "25" },
                    { label: "50", value: "50" },
                    { label: "100", value: "100" },
                    { label: t("members.presetPause"), value: "0" },
                  ].map((preset) => {
                    const active = aiLimitDraft.trim() === preset.value;
                    return (
                      <button
                        key={preset.label}
                        type="button"
                        disabled={savingLimits}
                        onClick={() => setAiLimitDraft(preset.value)}
                        className={cn(
                          "rounded-md border px-2 py-1 text-[11px] font-medium transition-colors",
                          active
                            ? "border-foreground bg-foreground text-background"
                            : "border-border bg-background text-muted-foreground hover:text-foreground",
                        )}
                      >
                        {preset.label}
                      </button>
                    );
                  })}
                </div>
                {aiLimitDraft.trim() === "0" && (
                  <p className="text-[11px] text-muted-foreground">
                    {t("members.aiLimitPausedHint")}
                  </p>
                )}
              </div>

              <DialogFooter>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => setLimitsTarget(null)}
                  disabled={savingLimits}
                >
                  {t("actions.cancel")}
                </Button>
                <Button type="submit" disabled={savingLimits}>
                  {savingLimits && <Loader2 className="size-4 animate-spin" />}
                  {savingLimits ? t("members.savingLimits") : t("members.saveLimits")}
                </Button>
              </DialogFooter>
            </form>
          )}
        </DialogContent>
      </Dialog>

      <Dialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{t("danger.deleteDialogTitle")}</DialogTitle>
            <DialogDescription>
              {t("danger.deleteDialogBody", { team: detail.team.name })}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="delete-team-confirm">
              {t("danger.deleteConfirmPrompt", { team: detail.team.name })}
            </Label>
            <Input
              id="delete-team-confirm"
              value={deleteConfirmText}
              onChange={(event) => setDeleteConfirmText(event.target.value)}
              placeholder={detail.team.name}
              disabled={deletingTeam}
            />
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setDeleteDialogOpen(false)}
              disabled={deletingTeam}
            >
              {t("actions.cancel")}
            </Button>
            <Button
              type="button"
              variant="destructive"
              disabled={deletingTeam || deleteConfirmText.trim() !== detail.team.name}
              onClick={() => void handleDeleteTeam()}
            >
              {deletingTeam && <Loader2 className="size-4 animate-spin" />}
              {deletingTeam ? t("danger.deleting") : t("danger.deleteAction")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </main>
  );
}
