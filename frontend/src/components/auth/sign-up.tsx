"use client";

import { useState } from "react";
import { signUp } from "../../lib/auth-client";
import { track } from "@/lib/datafast";
import { Button } from "../ui/button";
import { Input } from "../ui/input";

export function SignUp() {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setMessage("");

    const response = await signUp.email({
      email,
      password,
      name,
    });

    if (response.error) {
      setMessage(response.error.message || "Failed to create account");
      setLoading(false);
      return;
    }

    track("signup_completed", {
      auth_method: "email",
    });
    setMessage("Account created successfully! Signing you in...");
    setLoading(false);

    // Automatically sign in after successful sign up
    setTimeout(() => {
      window.location.href = "/";
    }, 1000);
  };

  return (
    <div className="w-full">
      <h1 className="font-display text-3xl font-bold tracking-tight">Create your account</h1>
      <p className="mt-2 text-sm text-muted-foreground">Start turning long videos into clips in minutes.</p>
      <div className="mt-8">
        <form onSubmit={handleSubmit} className="space-y-4">
          <Input
            className="h-11"
            type="text"
            placeholder="Full Name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            disabled={loading}
          />
          <Input
            className="h-11"
            type="email"
            placeholder="Email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            disabled={loading}
          />
          <Input
            className="h-11"
            type="password"
            placeholder="Password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            disabled={loading}
            minLength={8}
          />
          <Button type="submit" className="h-11 w-full" disabled={loading}>
            {loading ? "Creating Account..." : "Sign Up"}
          </Button>
        </form>
        {message && (
          <p className={`mt-4 text-sm ${message.includes("successfully") ? "text-green-600" : "text-red-600"}`}>
            {message}
          </p>
        )}
      </div>
    </div>
  );
}
