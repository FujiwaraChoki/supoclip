"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { resetPassword } from "../../lib/auth-client";
import { Button } from "../ui/button";
import { Input } from "../ui/input";

export function ResetPassword() {
  const t = useTranslations("auth");
  const searchParams = useSearchParams();
  const token = searchParams.get("token");
  const linkError = searchParams.get("error");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");
  const [done, setDone] = useState(false);
  const router = useRouter();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!token) return;
    if (password !== confirmPassword) {
      setMessage(t("reset.mismatch"));
      return;
    }

    setLoading(true);
    setMessage("");

    const response = await resetPassword({ newPassword: password, token });

    setLoading(false);
    if (response.error) {
      setMessage(response.error.message || t("reset.failed"));
      return;
    }

    setDone(true);
    setTimeout(() => router.push("/sign-in"), 1500);
  };

  if (!token || linkError) {
    return (
      <div className="w-full">
        <h1 className="font-display text-3xl font-bold tracking-tight">{t("reset.expiredTitle")}</h1>
        <p className="mt-2 text-sm text-muted-foreground">{t("reset.expiredBody")}</p>
        <Link href="/forgot-password" className="mt-8 inline-block text-sm font-medium underline-offset-4 hover:underline">
          {t("reset.requestNew")}
        </Link>
      </div>
    );
  }

  return (
    <div className="w-full">
      <h1 className="font-display text-3xl font-bold tracking-tight">{t("reset.title")}</h1>
      <p className="mt-2 text-sm text-muted-foreground">{t("reset.subtitle")}</p>
      <div className="mt-8">
        {done ? (
          <p className="text-sm text-green-600">{t("reset.success")}</p>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4">
            <Input
              className="h-11"
              type="password"
              placeholder={t("reset.newPassword")}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={8}
              disabled={loading}
            />
            <Input
              className="h-11"
              type="password"
              placeholder={t("reset.confirmPassword")}
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              required
              minLength={8}
              disabled={loading}
            />
            <Button type="submit" className="h-11 w-full" disabled={loading}>
              {loading ? t("reset.submitting") : t("reset.submit")}
            </Button>
          </form>
        )}
        {message && <p className="mt-4 text-sm text-red-600">{message}</p>}
      </div>
    </div>
  );
}
