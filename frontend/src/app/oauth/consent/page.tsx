"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import Image from "next/image";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { AlertCircle, Check, Plug } from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useSession } from "@/lib/auth-client";
import { signInPath } from "@/lib/auth-redirect";

interface ConsentRequest {
  client_name: string;
  redirect_host: string;
  email_verified: boolean;
  at_key_limit: boolean;
  max_keys: number;
}

type ErrorCode = "request_expired" | "key_limit" | "email_not_verified" | "generic";

function errorCode(body: unknown): ErrorCode {
  const code = (body as { detail?: { code?: string } } | null)?.detail?.code;
  return code === "request_expired" || code === "key_limit" || code === "email_not_verified" ? code : "generic";
}

function Frame({ children }: { children: React.ReactNode }) {
  return (
    <main className="flex min-h-dvh flex-col bg-background px-6 py-8">
      <Link href="/" className="flex items-center gap-2 font-display text-lg font-bold tracking-tight">
        <Image src="/logo.png" alt="" width={24} height={24} className="size-6 dark:invert" priority />SupoClip
      </Link>
      <div className="mx-auto flex w-full max-w-md flex-1 flex-col justify-center py-12">{children}</div>
    </main>
  );
}

function Consent() {
  const t = useTranslations("auth.oauthConsent");
  const router = useRouter();
  const requestId = useSearchParams().get("request") || "";
  const { data: session, isPending } = useSession();
  const [request, setRequest] = useState<ConsentRequest | null>(null);
  const [error, setError] = useState<ErrorCode | null>(null);
  const [submitting, setSubmitting] = useState<"allow" | "deny" | null>(null);
  const [redirecting, setRedirecting] = useState(false);
  const signedIn = Boolean(session?.user);

  useEffect(() => {
    if (!isPending && !signedIn) {
      router.replace(signInPath(window.location.pathname + window.location.search));
    }
  }, [isPending, signedIn, router]);

  const load = useCallback(async () => {
    if (!requestId) {
      setError("request_expired");
      return;
    }
    try {
      const response = await fetch(`/api/oauth/requests/${encodeURIComponent(requestId)}`, { cache: "no-store" });
      const body = await response.json().catch(() => null);
      if (!response.ok) {
        setError(errorCode(body));
        return;
      }
      setRequest(body as ConsentRequest);
    } catch {
      setError("generic");
    }
  }, [requestId]);

  useEffect(() => {
    if (signedIn) void load();
  }, [signedIn, load]);

  const decide = async (approve: boolean) => {
    setSubmitting(approve ? "allow" : "deny");
    setError(null);
    try {
      const response = await fetch(`/api/oauth/requests/${encodeURIComponent(requestId)}/decision`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ approve }),
      });
      const body = await response.json().catch(() => null);
      if (!response.ok || typeof body?.redirect_url !== "string") {
        setError(errorCode(body));
        setSubmitting(null);
        return;
      }
      setRedirecting(true);
      window.location.assign(body.redirect_url);
    } catch {
      setError("generic");
      setSubmitting(null);
    }
  };

  if (isPending || (!signedIn && !error)) {
    return <Skeleton className="h-72 w-full" />;
  }

  if (error === "request_expired" || (error === "generic" && !request)) {
    return (
      <div>
        <h1 className="font-display text-3xl font-bold tracking-tight">{t(`errors.${error}.title`)}</h1>
        <p className="mt-2 text-sm text-muted-foreground">{t(`errors.${error}.body`)}</p>
      </div>
    );
  }

  if (!request) return <Skeleton className="h-72 w-full" />;

  const name = request.client_name;
  const blocked = !request.email_verified || request.at_key_limit;
  const capabilities = ["generations", "clips", "minutes"] as const;

  return (
    <div>
      <span className="flex size-11 items-center justify-center rounded-xl border bg-muted">
        <Plug className="size-5" aria-hidden />
      </span>
      <h1 className="mt-5 font-display text-2xl font-bold tracking-tight sm:text-3xl">{t("title", { name })}</h1>
      <p className="mt-2 text-sm text-muted-foreground">{t("signedInAs", { email: session?.user?.email ?? "" })}</p>

      <div className="mt-6 rounded-lg border p-4">
        <p className="text-sm font-medium">{t("canTitle", { name })}</p>
        <ul className="mt-3 space-y-2">
          {capabilities.map((item) => (
            <li key={item} className="flex items-start gap-2 text-sm">
              <Check className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
              {t(`can.${item}`)}
            </li>
          ))}
        </ul>
      </div>

      <p className="mt-4 text-xs text-muted-foreground">
        {t.rich("keyNote", {
          key: t("keyName", { name }),
          strong: (chunks) => <span className="font-medium text-foreground">{chunks}</span>,
          settings: (chunks) => <Link href="/settings/api-keys" className="underline underline-offset-4">{chunks}</Link>,
        })}
      </p>
      <p className="mt-2 text-xs text-muted-foreground">
        {t.rich("redirectNote", {
          host: request.redirect_host,
          strong: (chunks) => <span className="font-medium text-foreground">{chunks}</span>,
        })}
      </p>

      {!request.email_verified && (
        <Alert className="mt-6">
          <AlertCircle className="size-4" />
          <AlertDescription>
            <p>
              {t.rich("emailNotVerified", {
                settings: (chunks) => <Link href="/settings/api-keys" className="font-medium underline underline-offset-4">{chunks}</Link>,
              })}
            </p>
          </AlertDescription>
        </Alert>
      )}
      {request.email_verified && request.at_key_limit && (
        <Alert className="mt-6">
          <AlertCircle className="size-4" />
          <AlertDescription>
            <p>
              {t.rich("keyLimit", {
                max: request.max_keys,
                settings: (chunks) => <Link href="/settings/api-keys" className="font-medium underline underline-offset-4">{chunks}</Link>,
              })}
            </p>
          </AlertDescription>
        </Alert>
      )}
      {error && (
        <Alert variant="destructive" className="mt-6" role="alert">
          <AlertCircle className="size-4" />
          <AlertDescription>{t(`errors.${error}.body`, { max: request.max_keys })}</AlertDescription>
        </Alert>
      )}

      {redirecting ? (
        <p role="status" className="mt-8 text-sm text-muted-foreground">{t("redirecting", { host: request.redirect_host })}</p>
      ) : (
        <div className="mt-8 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <Button variant="outline" className="h-11 sm:w-28" onClick={() => void decide(false)} disabled={submitting !== null}>
            {submitting === "deny" ? t("denying") : t("deny")}
          </Button>
          <Button className="h-11 sm:w-28" onClick={() => void decide(true)} disabled={submitting !== null || blocked}>
            {submitting === "allow" ? t("allowing") : t("allow")}
          </Button>
        </div>
      )}
    </div>
  );
}

export default function OAuthConsentPage() {
  return (
    <Frame>
      <Suspense fallback={<Skeleton className="h-72 w-full" />}>
        <Consent />
      </Suspense>
    </Frame>
  );
}
