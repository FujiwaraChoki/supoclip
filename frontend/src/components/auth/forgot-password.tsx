"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { requestPasswordReset } from "../../lib/auth-client";
import { Button } from "../ui/button";
import { Input } from "../ui/input";

export function ForgotPassword() {
  const t = useTranslations("auth");
  const [email, setEmail] = useState("");
  const [loading, setLoading] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState("");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError("");

    const response = await requestPasswordReset({
      email,
      redirectTo: "/reset-password",
    });

    setLoading(false);
    if (response.error) {
      setError(response.error.message || t("forgot.failed"));
      return;
    }
    setSent(true);
  };

  return (
    <div className="w-full">
      <h1 className="font-display text-3xl font-bold tracking-tight">{t("forgot.title")}</h1>
      <p className="mt-2 text-sm text-muted-foreground">{t("forgot.subtitle")}</p>
      <div className="mt-8">
        {sent ? (
          <p className="text-sm text-green-600">
            {t("forgot.sent", { email })}
          </p>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4">
            <Input
              className="h-11"
              type="email"
              placeholder={t("forgot.email")}
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              disabled={loading}
            />
            <Button type="submit" className="h-11 w-full" disabled={loading}>
              {loading ? t("forgot.submitting") : t("forgot.submit")}
            </Button>
          </form>
        )}
        {error && <p className="mt-4 text-sm text-red-600">{error}</p>}
      </div>
    </div>
  );
}
