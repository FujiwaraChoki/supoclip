"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { toast } from "@/lib/toast";

interface LlmStatus {
  ollama_connected: boolean;
  gemini_key_set: boolean;
}

export function LlmConnectionTest({ status }: { status: LlmStatus | null }) {
  const [testingOllama, setTestingOllama] = useState(false);
  const [testingGemini, setTestingGemini] = useState(false);

  const testOllama = async () => {
    setTestingOllama(true);
    try {
      const response = await fetch("/api/admin/test-ollama-connection", { method: "POST" });
      const data = await response.json();
      if (data.reachable) {
        toast.success(`Ollama connected (v${data.version ?? "?"}, ${data.models?.length ?? 0} models).`);
      } else {
        toast.error(`Ollama unreachable: ${data.error ?? "unknown error"}`);
      }
    } catch {
      toast.error("Failed to test Ollama connection.");
    } finally {
      setTestingOllama(false);
    }
  };

  const testGemini = async () => {
    setTestingGemini(true);
    try {
      const response = await fetch("/api/admin/test-gemini-connection", { method: "POST" });
      const data = await response.json();
      if (data.ok) {
        toast.success("Gemini connected.");
      } else {
        toast.error(`Gemini test failed: ${data.error ?? "unknown error"}`);
      }
    } catch {
      toast.error("Failed to test Gemini connection.");
    } finally {
      setTestingGemini(false);
    }
  };

  const statusLabel = !status
    ? "Checking..."
    : status.ollama_connected
      ? "● Ollama connected"
      : status.gemini_key_set
        ? "○ Gemini key set"
        : "○ Both unavailable";

  return (
    <div className="space-y-2 px-4 py-3">
      <p className="text-xs font-medium text-muted-foreground">{statusLabel}</p>
      <div className="flex gap-2">
        <Button variant="outline" size="sm" onClick={testOllama} disabled={testingOllama}>
          {testingOllama ? "Testing..." : "Test Ollama connection"}
        </Button>
        <Button variant="outline" size="sm" onClick={testGemini} disabled={testingGemini}>
          {testingGemini ? "Testing..." : "Test Gemini connection"}
        </Button>
      </div>
    </div>
  );
}
