"use client";

import { useState } from "react";
import { requestPasswordReset } from "../../lib/auth-client";
import { Button } from "../ui/button";
import { Input } from "../ui/input";

export function ForgotPassword() {
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
      setError(response.error.message || "Could not send reset link");
      return;
    }
    setSent(true);
  };

  return (
    <div className="w-full">
      <h1 className="font-display text-3xl font-bold tracking-tight">Forgot your password?</h1>
      <p className="mt-2 text-sm text-muted-foreground">
        Enter your email and we&apos;ll send you a link to reset it.
      </p>
      <div className="mt-8">
        {sent ? (
          <p className="text-sm text-green-600">
            If an account exists for {email}, a reset link is on its way. Check your inbox and spam folder.
          </p>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4">
            <Input
              className="h-11"
              type="email"
              placeholder="Email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              disabled={loading}
            />
            <Button type="submit" className="h-11 w-full" disabled={loading}>
              {loading ? "Sending..." : "Send reset link"}
            </Button>
          </form>
        )}
        {error && <p className="mt-4 text-sm text-red-600">{error}</p>}
      </div>
    </div>
  );
}
