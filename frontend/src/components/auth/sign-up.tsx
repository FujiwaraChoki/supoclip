"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { signUp } from "../../lib/auth-client";
import { trackEvent } from "@/lib/analytics";
import { attributionMetadata, getAnonymousAttribution } from "@/lib/attribution";
import { postAuthPath } from "@/lib/auth-redirect";
import { track } from "@/lib/datafast";
import { Button } from "../ui/button";
import { Input } from "../ui/input";

export function SignUp() {
  const t = useTranslations("auth");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");
  const [ok, setOk] = useState(false);
  const [sentTo, setSentTo] = useState<string | null>(null);

  useEffect(() => {
    const invitedEmail = new URLSearchParams(window.location.search).get("email")?.trim();
    if (invitedEmail) {
      setEmail((current) => current || invitedEmail);
    }
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setMessage("");
    setOk(false);

    const next = postAuthPath(window.location.search);
    const response = await signUp.email({
      email,
      password,
      name,
      // Where the verification link lands, signed in, when the server requires one.
      callbackURL: next,
    });

    if (response.error) {
      setMessage(response.error.message || t(response.error.status >= 500 ? "signUp.serverError" : "signUp.failed"));
      setLoading(false);
      return;
    }

    track("signup_completed", {
      auth_method: "email",
      ...attributionMetadata(getAnonymousAttribution()),
    });
    trackEvent("signup_completed", { method: "email" });
    setLoading(false);

    // No session means this server wants the address verified first.
    if (!response.data?.token) {
      setSentTo(email);
      return;
    }

    setOk(true);
    setMessage(t("signUp.success"));
    setTimeout(() => {
      window.location.href = next;
    }, 1000);
  };

  if (sentTo) {
    return (
      <div className="w-full">
        <h1 className="font-display text-3xl font-bold tracking-tight">{t("signUp.verifyTitle")}</h1>
        <p className="mt-2 text-sm text-muted-foreground">{t("signUp.verifyBody", { email: sentTo })}</p>
        <p className="mt-4 text-sm text-muted-foreground">{t("signUp.verifyHint")}</p>
      </div>
    );
  }

  return (
    <div className="w-full">
      <h1 className="font-display text-3xl font-bold tracking-tight">{t("signUp.title")}</h1>
      <p className="mt-2 text-sm text-muted-foreground">{t("signUp.subtitle")}</p>
      <div className="mt-8">
        <form onSubmit={handleSubmit} className="space-y-4">
          <Input
            className="h-11"
            type="text"
            placeholder={t("signUp.name")}
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            disabled={loading}
          />
          <Input
            className="h-11"
            type="email"
            placeholder={t("signUp.email")}
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            disabled={loading}
          />
          <Input
            className="h-11"
            type="password"
            placeholder={t("signUp.password")}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            disabled={loading}
            minLength={8}
          />
          <Button type="submit" className="h-11 w-full" disabled={loading}>
            {loading ? t("signUp.submitting") : t("signUp.submit")}
          </Button>
        </form>
        {message && (
          <p className={`mt-4 text-sm ${ok ? "text-green-600" : "text-red-600"}`}>
            {message}
          </p>
        )}
      </div>
    </div>
  );
}
