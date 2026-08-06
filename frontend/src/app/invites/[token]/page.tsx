"use client";

import { useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export default function AcceptWorkspaceInvitePage() {
  const { token } = useParams<{ token: string }>();
  const [status, setStatus] = useState<"idle" | "loading" | "accepted" | "error">("idle");
  const [message, setMessage] = useState("");

  const accept = async () => {
    setStatus("loading");
    const response = await fetch(`/api/workflows/invites/${token}/accept`, { method: "POST" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) { setStatus("error"); setMessage(data.detail || data.error || "Unable to accept invitation"); return; }
    setStatus("accepted"); setMessage(`You joined the workspace as ${data.role}.`);
  };

  return <main className="grid min-h-screen place-items-center bg-stone-50 p-4"><Card className="w-full max-w-md"><CardHeader><CardTitle>Join SupoClip workspace</CardTitle></CardHeader><CardContent className="space-y-4"><p className="text-sm text-stone-600">Accept this invitation using the SupoClip account with the invited email address.</p>{message && <Alert variant={status === "error" ? "destructive" : "default"}><AlertDescription>{message}</AlertDescription></Alert>}{status === "accepted" ? <Button asChild><Link href="/list">Open generations</Link></Button> : <Button disabled={status === "loading"} onClick={accept}>{status === "loading" ? "Joining…" : "Accept invitation"}</Button>}</CardContent></Card></main>;
}
