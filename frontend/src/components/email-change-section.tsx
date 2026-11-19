"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { AlertCircle, AtSign, CheckCircle2, Loader2 } from "lucide-react";

import { track } from "@/lib/datafast";
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

type Stage = "verify_current" | "enter_new" | "verify_new" | "finishing";

interface EmailChangeState {
  email: string;
  emailVerified: boolean;
  hasPassword: boolean;
  request: {
    stage: Stage;
    newEmail: string | null;
    codeExpiresAt: string | null;
    resendAvailableAt: string | null;
    expiresAt: string;
  } | null;
}

type Step = "loading" | "intro" | Stage | "done";

const KNOWN_ERRORS = new Set([
  "password_required",
  "invalid_password",
  "reauth_required",
  "rate_limited",
  "cooldown",
  "no_request",
  "wrong_code",
  "code_expired",
  "too_many_attempts",
  "invalid_email",
  "same_email",
  "email_taken",
  "send_failed",
  "billing_update_failed",
  "in_progress",
]);

interface ApiError {
  error?: string;
  attemptsLeft?: number;
  retryAfterSeconds?: number;
}

interface EmailChangeSectionProps {
  email: string;
  emailVerified: boolean;
  /** Called after the switch so the page picks up the new address. */
  onChanged?: (email: string) => void;
}

export function EmailChangeSection({ email, emailVerified, onChanged }: EmailChangeSectionProps) {
  const t = useTranslations("settings");
  const common = useTranslations("common");
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState<Step>("loading");
  const [state, setState] = useState<EmailChangeState | null>(null);
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [newEmail, setNewEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<{ email: string; previous: string } | null>(null);
  const [now, setNow] = useState(() => Date.now());

  const describeError = useCallback(
    (data: ApiError) => {
      const key = data.error && KNOWN_ERRORS.has(data.error) ? camel(data.error) : "generic";
      if (key === "wrongCode" && data.attemptsLeft !== undefined) {
        return t("emailChange.errors.wrongCodeLeft", { count: data.attemptsLeft });
      }
      if (key === "rateLimited" || key === "cooldown") {
        const seconds = data.retryAfterSeconds ?? 60;
        return t(`emailChange.errors.${key}`, { minutes: Math.max(1, Math.ceil(seconds / 60)), seconds });
      }
      return t(`emailChange.errors.${key}`);
    },
    [t],
  );

  const apply = useCallback((next: EmailChangeState) => {
    setState(next);
    setStep(next.request?.stage ?? "intro");
    setCode("");
  }, []);

  const call = useCallback(
    async (method: "GET" | "POST" | "DELETE", body?: Record<string, unknown>) => {
      const response = await fetch("/api/account/email-change", {
        method,
        cache: "no-store",
        headers: body ? { "Content-Type": "application/json" } : undefined,
        body: body ? JSON.stringify(body) : undefined,
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw data as ApiError;
      return data;
    },
    [],
  );

  // Resume an open request (e.g. after checking mail in another tab).
  useEffect(() => {
    if (!open || step !== "loading") return;
    call("GET")
      .then((data: EmailChangeState) => {
        apply(data);
        // A finish that was already running (see in_progress) may have switched it meanwhile.
        if (data.email !== email) onChanged?.(data.email);
      })
      .catch((data: ApiError) => {
        setError(describeError(data));
        setStep("intro");
      });
  }, [open, step, call, apply, describeError, email, onChanged]);

  useEffect(() => {
    if (!open) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [open]);

  const run = async (body: Record<string, unknown>) => {
    setBusy(true);
    setError(null);
    try {
      const data = await call("POST", body);
      if (data.done) {
        const previous = state?.email ?? email;
        setDone({ email: data.done.email, previous });
        setStep("done");
        track("email_changed");
        onChanged?.(data.done.email);
      } else {
        apply(data as EmailChangeState);
      }
    } catch (data) {
      const failure = data as ApiError;
      setError(describeError(failure));
      // These leave the request in another step: show that step.
      if (failure.error === "no_request" || failure.error === "in_progress") setStep("loading");
      if (failure.error === "email_taken") setStep("enter_new");
      if (failure.error === "billing_update_failed") setStep("finishing");
    } finally {
      setBusy(false);
    }
  };

  const handleOpenChange = (next: boolean) => {
    if (busy) return;
    setOpen(next);
    if (next) {
      setStep("loading");
      setError(null);
      setDone(null);
    } else {
      setPassword("");
      setCode("");
    }
  };

  const cancel = async () => {
    if (state?.request) await call("DELETE").catch(() => undefined);
    handleOpenChange(false);
  };

  const current = state?.email ?? email;
  const resendAt = state?.request?.resendAvailableAt ? Date.parse(state.request.resendAvailableAt) : 0;
  const resendIn = Math.max(0, Math.ceil((resendAt - now) / 1000));
  const codeReady = /^\d{6}$/.test(code.replace(/\s+/g, ""));

  const codeField = (target: string, action: "verify_current" | "verify_new") => (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault();
        if (codeReady && !busy) void run({ action, code });
      }}
    >
      <p className="text-sm text-muted-foreground">
        {t.rich("emailChange.codeSent", { email: target, strong: (chunks) => <span className="font-medium text-foreground">{chunks}</span> })}
      </p>
      <div className="space-y-2">
        <Label htmlFor="email-change-code">{t("emailChange.codeLabel")}</Label>
        <Input
          id="email-change-code"
          value={code}
          onChange={(event) => setCode(event.target.value.replace(/[^\d\s]/g, "").slice(0, 7))}
          inputMode="numeric"
          autoComplete="one-time-code"
          placeholder="123456"
          className="max-w-40 font-mono text-lg tracking-[0.3em]"
          autoFocus
          disabled={busy}
        />
        <p className="text-xs text-muted-foreground">{t("emailChange.codeHint")}</p>
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
        <button
          type="button"
          className="underline underline-offset-4 disabled:cursor-not-allowed disabled:no-underline disabled:opacity-60"
          onClick={() => void run({ action: "resend" })}
          disabled={busy || resendIn > 0}
        >
          {resendIn > 0 ? t("emailChange.resendIn", { seconds: resendIn }) : t("emailChange.resend")}
        </button>
        {action === "verify_new" && (
          <button type="button" className="underline underline-offset-4" onClick={() => { setError(null); setStep("enter_new"); }} disabled={busy}>
            {t("emailChange.useDifferent")}
          </button>
        )}
      </div>
      <DialogFooter>
        <Button type="button" variant="outline" onClick={cancel} disabled={busy}>{common("cancel")}</Button>
        <Button type="submit" disabled={!codeReady || busy}>
          {busy && <Loader2 className="size-4 animate-spin" />}
          {action === "verify_current" ? t("emailChange.verify") : t("emailChange.confirm")}
        </Button>
      </DialogFooter>
    </form>
  );

  return (
    <section id="account" className="scroll-mt-6 rounded-2xl border bg-background">
      <div className="flex items-start gap-3 border-b px-5 py-4">
        <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg bg-muted"><AtSign className="size-4" /></span>
        <div>
          <h2 className="text-sm font-semibold">{t("emailChange.title")}</h2>
          <p className="text-xs text-muted-foreground">{t("emailChange.description")}</p>
        </div>
      </div>
      <div className="flex flex-col gap-4 p-5 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <p className="text-xs text-muted-foreground">{t("emailChange.label")}</p>
          <p className="flex flex-wrap items-center gap-2 text-sm font-medium">
            <span className="truncate">{email}</span>
            <Badge variant={emailVerified ? "secondary" : "outline"}>
              {emailVerified ? t("emailChange.verified") : t("emailChange.unverified")}
            </Badge>
          </p>
        </div>
        <Button type="button" variant="outline" onClick={() => handleOpenChange(true)}>{t("emailChange.action")}</Button>
      </div>

      <Dialog open={open} onOpenChange={handleOpenChange}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{step === "done" ? t("emailChange.doneTitle") : t("emailChange.dialogTitle")}</DialogTitle>
            {step !== "done" && step !== "loading" && (
              <DialogDescription>
                {t("emailChange.stepOf", { step: step === "intro" || step === "verify_current" ? 1 : 2 })}
              </DialogDescription>
            )}
          </DialogHeader>

          {error && (
            <Alert className="border-red-200 bg-red-50 dark:border-red-900/60 dark:bg-red-950/40">
              <AlertCircle className="h-4 w-4 text-red-500" />
              <AlertDescription className="text-sm text-red-700 dark:text-red-300">{error}</AlertDescription>
            </Alert>
          )}

          {step === "loading" && (
            <div role="status" className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" />{common("loading")}
            </div>
          )}

          {step === "intro" && state && (
            <form
              className="space-y-4"
              onSubmit={(event) => {
                event.preventDefault();
                if (!busy && (!state.hasPassword || password)) void run({ action: "start", password });
              }}
            >
              <p className="text-sm text-muted-foreground">
                {t.rich("emailChange.intro", { email: current, strong: (chunks) => <span className="font-medium text-foreground">{chunks}</span> })}
              </p>
              {!state.emailVerified && (
                <p className="rounded-lg border px-3 py-2 text-xs text-muted-foreground">{t("emailChange.unverifiedNote")}</p>
              )}
              {state.hasPassword ? (
                <div className="space-y-2">
                  <Label htmlFor="email-change-password">{t("emailChange.password")}</Label>
                  <Input
                    id="email-change-password"
                    type="password"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    autoComplete="current-password"
                    autoFocus
                    disabled={busy}
                  />
                </div>
              ) : (
                <p className="text-xs text-muted-foreground">{t("emailChange.noPassword")}</p>
              )}
              <DialogFooter>
                <Button type="button" variant="outline" onClick={() => handleOpenChange(false)} disabled={busy}>{common("cancel")}</Button>
                <Button type="submit" disabled={busy || (state.hasPassword && !password)}>
                  {busy && <Loader2 className="size-4 animate-spin" />}
                  {t("emailChange.sendCurrent")}
                </Button>
              </DialogFooter>
            </form>
          )}

          {step === "verify_current" && codeField(current, "verify_current")}

          {step === "enter_new" && (
            <form
              className="space-y-4"
              onSubmit={(event) => {
                event.preventDefault();
                if (!busy && newEmail.trim()) void run({ action: "new_email", email: newEmail });
              }}
            >
              <p className="flex items-center gap-2 text-sm text-emerald-700 dark:text-emerald-400">
                <CheckCircle2 className="size-4" />{t("emailChange.currentConfirmed")}
              </p>
              <div className="space-y-2">
                <Label htmlFor="email-change-new">{t("emailChange.newLabel")}</Label>
                <Input
                  id="email-change-new"
                  type="email"
                  value={newEmail}
                  onChange={(event) => setNewEmail(event.target.value)}
                  autoComplete="email"
                  placeholder="you@example.com"
                  autoFocus
                  disabled={busy}
                />
                <p className="text-xs text-muted-foreground">{t("emailChange.newHint")}</p>
              </div>
              <DialogFooter>
                <Button type="button" variant="outline" onClick={cancel} disabled={busy}>{common("cancel")}</Button>
                <Button type="submit" disabled={busy || !newEmail.trim()}>
                  {busy && <Loader2 className="size-4 animate-spin" />}
                  {t("emailChange.sendNew")}
                </Button>
              </DialogFooter>
            </form>
          )}

          {step === "verify_new" && codeField(state?.request?.newEmail ?? newEmail, "verify_new")}

          {step === "finishing" && (
            <div className="space-y-4">
              <p className="text-sm text-muted-foreground">{t("emailChange.finishing")}</p>
              <DialogFooter>
                <Button type="button" variant="outline" onClick={cancel} disabled={busy}>{common("cancel")}</Button>
                <Button type="button" onClick={() => void run({ action: "finish" })} disabled={busy}>
                  {busy && <Loader2 className="size-4 animate-spin" />}
                  {t("emailChange.retry")}
                </Button>
              </DialogFooter>
            </div>
          )}

          {step === "done" && done && (
            <div className="space-y-4">
              <p className="flex items-start gap-2 text-sm">
                <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-600" />
                <span>{t.rich("emailChange.done", { email: done.email, strong: (chunks) => <span className="font-medium">{chunks}</span> })}</span>
              </p>
              <p className="text-sm text-muted-foreground">{t("emailChange.doneDetails", { previous: done.previous })}</p>
              <DialogFooter>
                <Button type="button" onClick={() => handleOpenChange(false)}>{t("emailChange.close")}</Button>
              </DialogFooter>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </section>
  );
}

/** wrong_code → wrongCode, for message keys. */
function camel(code: string) {
  return code.replace(/_([a-z])/g, (_, letter: string) => letter.toUpperCase());
}
