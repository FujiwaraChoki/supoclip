"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { resetPassword } from "../../lib/auth-client";
import { Button } from "../ui/button";
import { Input } from "../ui/input";

export function ResetPassword() {
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
      setMessage("Passwords do not match");
      return;
    }

    setLoading(true);
    setMessage("");

    const response = await resetPassword({ newPassword: password, token });

    setLoading(false);
    if (response.error) {
      setMessage(response.error.message || "Failed to reset password");
      return;
    }

    setDone(true);
    setTimeout(() => router.push("/sign-in"), 1500);
  };

  if (!token || linkError) {
    return (
      <div className="w-full">
        <h1 className="font-display text-3xl font-bold tracking-tight">Link expired</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          This password reset link is invalid or has expired.
        </p>
        <Link href="/forgot-password" className="mt-8 inline-block text-sm font-medium underline-offset-4 hover:underline">
          Request a new link
        </Link>
      </div>
    );
  }

  return (
    <div className="w-full">
      <h1 className="font-display text-3xl font-bold tracking-tight">Choose a new password</h1>
      <p className="mt-2 text-sm text-muted-foreground">You&apos;ll be signed out on other devices.</p>
      <div className="mt-8">
        {done ? (
          <p className="text-sm text-green-600">Password updated. Redirecting to sign in...</p>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4">
            <Input
              className="h-11"
              type="password"
              placeholder="New password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={8}
              disabled={loading}
            />
            <Input
              className="h-11"
              type="password"
              placeholder="Confirm new password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              required
              minLength={8}
              disabled={loading}
            />
            <Button type="submit" className="h-11 w-full" disabled={loading}>
              {loading ? "Saving..." : "Reset password"}
            </Button>
          </form>
        )}
        {message && <p className="mt-4 text-sm text-red-600">{message}</p>}
      </div>
    </div>
  );
}
