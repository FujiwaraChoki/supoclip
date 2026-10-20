"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { signIn } from "../../lib/auth-client";
import { postAuthPath } from "@/lib/auth-redirect";
import { track } from "@/lib/datafast";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { useRouter } from "next/navigation";
import Link from "next/link";

export function SignIn() {
  const t = useTranslations("auth");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");
  const [ok, setOk] = useState(false);
  const router = useRouter();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setMessage("");
    setOk(false);

    const response = await signIn.email({
      email,
      password,
    });

    if (response.error) {
      setMessage(response.error.message || t("signIn.failed"));
      setLoading(false);
      return;
    }

    track("signin_completed", {
      auth_method: "email",
    });
    setOk(true);
    setMessage(t("signIn.success"));
    setLoading(false);

    // Redirect after successful sign in
    setTimeout(() => {
      router.push(postAuthPath(window.location.search));
      router.refresh();
    }, 500);
  };

  return (
    <div className="w-full">
      <h1 className="font-display text-3xl font-bold tracking-tight">{t("signIn.title")}</h1>
      <p className="mt-2 text-sm text-muted-foreground">{t("signIn.subtitle")}</p>
      <div className="mt-8">
        <form onSubmit={handleSubmit} className="space-y-4">
          <Input
            className="h-11"
            type="email"
            placeholder={t("signIn.email")}
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            disabled={loading}
          />
          <Input
            className="h-11"
            type="password"
            placeholder={t("signIn.password")}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            disabled={loading}
          />
          <div className="flex justify-end">
            <Link href="/forgot-password" className="text-sm text-muted-foreground underline-offset-4 hover:text-foreground hover:underline">
              {t("signIn.forgot")}
            </Link>
          </div>
          <Button type="submit" className="h-11 w-full" disabled={loading}>
            {loading ? t("signIn.submitting") : t("signIn.submit")}
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
