"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";

interface KeyMark {
  fingerprint: string;
  source: "admin" | "environment";
}

interface Attempt {
  model: string;
  provider: string;
  key_fingerprint: string | null;
  seconds: number;
  ok: boolean;
  kind: string | null;
  error: string;
}

interface LlmSettings {
  primary: string;
  fallbacks: string[];
  chain: string[];
  providers: string[];
  keys: Record<string, KeyMark[]>;
}

interface ProbeResult {
  ok: boolean;
  seconds?: number;
  answer?: string;
  error?: string;
  answered_by?: string | null;
  attempts?: Attempt[];
}

/**
 * The chain of models and the keys of the providers.
 *
 * A busy model is left behind for the next one of the chain, so the chain is
 * what keeps the product working while one provider is overloaded.
 */
export function LlmSettingsPanel() {
  const t = useTranslations("admin");
  const [settings, setSettings] = useState<LlmSettings | null>(null);
  const [fallbacks, setFallbacks] = useState<string[]>([]);
  const [newModel, setNewModel] = useState("");
  const [newKey, setNewKey] = useState("");
  const [keyProvider, setKeyProvider] = useState("google");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [probe, setProbe] = useState<ProbeResult | null>(null);
  const [probeModel, setProbeModel] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const response = await fetch("/api/admin/llm", { cache: "no-store" });
      if (!response.ok) throw new Error(t("llm.loadFailed"));
      const data = (await response.json()) as LlmSettings;
      setSettings(data);
      setFallbacks(data.fallbacks);
      if (!data.providers.includes(keyProvider)) {
        setKeyProvider(data.providers[0] ?? "google");
      }
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : t("llm.loadFailed"));
    }
  }, [keyProvider, t]);

  useEffect(() => {
    void load();
  }, [load]);

  async function saveChain() {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const response = await fetch("/api/admin/llm/chain", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fallbacks }),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => null);
        throw new Error(data?.detail || t("llm.saveFailed"));
      }
      setMessage(t("llm.saved"));
      await load();
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : t("llm.saveFailed"));
    } finally {
      setBusy(false);
    }
  }

  async function addModel() {
    const model = newModel.trim();
    if (!model || fallbacks.includes(model)) return;
    setFallbacks((current) => [...current, model]);
    setNewModel("");
  }

  async function addKey() {
    const key = newKey.trim();
    if (!key) return;
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const response = await fetch("/api/admin/llm/keys", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider: keyProvider, key }),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => null);
        throw new Error(data?.detail || t("llm.keyFailed"));
      }
      setNewKey("");
      setMessage(t("llm.keyAdded"));
      await load();
    } catch (keyError) {
      setError(keyError instanceof Error ? keyError.message : t("llm.keyFailed"));
    } finally {
      setBusy(false);
    }
  }

  async function removeKey(fingerprint: string) {
    setBusy(true);
    setError(null);
    try {
      const response = await fetch("/api/admin/llm/keys", {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider: keyProvider, fingerprint }),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => null);
        throw new Error(data?.detail || t("llm.keyFailed"));
      }
      await load();
    } catch (keyError) {
      setError(keyError instanceof Error ? keyError.message : t("llm.keyFailed"));
    } finally {
      setBusy(false);
    }
  }

  async function testModel(model: string) {
    setProbeModel(model);
    setProbe(null);
    setError(null);
    try {
      const response = await fetch("/api/admin/llm/test/model", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model }),
      });
      const data = (await response.json()) as ProbeResult;
      setProbe(data);
    } catch (probeError) {
      setError(
        probeError instanceof Error ? probeError.message : t("llm.testFailed"),
      );
    } finally {
      setProbeModel(null);
    }
  }

  async function runChain() {
    setProbeModel(null);
    setProbe(null);
    setError(null);
    try {
      const response = await fetch("/api/admin/llm/test/chain", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      });
      const data = (await response.json()) as ProbeResult;
      setProbe(data);
    } catch (probeError) {
      setError(
        probeError instanceof Error ? probeError.message : t("llm.testFailed"),
      );
    }
  }

  const keyRows = settings?.keys?.[keyProvider] ?? [];

  return (
    <div className="divide-y">
      <div className="grid gap-4 px-5 py-5 lg:grid-cols-2">
        <section>
          <h3 className="text-sm font-medium">{t("llm.chainTitle")}</h3>
          <p className="mt-1 text-xs text-muted-foreground">
            {t("llm.chainDescription")}
          </p>

          <div className="mt-3 space-y-2">
            <div className="flex items-center justify-between gap-2 rounded-lg border bg-muted/40 px-3 py-2">
              <code className="text-xs">{settings?.primary ?? "—"}</code>
              <Badge variant="secondary">{t("llm.primary")}</Badge>
            </div>
            {fallbacks.map((model, index) => (
              <div
                key={model}
                className="flex items-center justify-between gap-2 rounded-lg border px-3 py-2"
              >
                <code className="text-xs">{model}</code>
                <div className="flex items-center gap-1">
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={busy || index === 0}
                    onClick={() =>
                      setFallbacks((current) => {
                        const next = [...current];
                        [next[index - 1], next[index]] = [next[index], next[index - 1]];
                        return next;
                      })
                    }
                  >
                    ↑
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={busy || index === fallbacks.length - 1}
                    onClick={() =>
                      setFallbacks((current) => {
                        const next = [...current];
                        [next[index + 1], next[index]] = [next[index], next[index + 1]];
                        return next;
                      })
                    }
                  >
                    ↓
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={busy}
                    onClick={() =>
                      setFallbacks((current) =>
                        current.filter((entry) => entry !== model),
                      )
                    }
                  >
                    {t("llm.remove")}
                  </Button>
                </div>
              </div>
            ))}
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Input
              value={newModel}
              onChange={(event) => setNewModel(event.target.value)}
              placeholder={t("llm.modelPlaceholder")}
              className="h-9 min-w-56 flex-1 font-mono text-xs"
            />
            <Button variant="secondary" onClick={addModel} disabled={busy}>
              {t("llm.addModel")}
            </Button>
            <Button onClick={saveChain} disabled={busy}>
              {t("llm.save")}
            </Button>
          </div>
        </section>

        <section>
          <h3 className="text-sm font-medium">{t("llm.keysTitle")}</h3>
          <p className="mt-1 text-xs text-muted-foreground">
            {t("llm.keysDescription")}
          </p>

          <div className="mt-3 flex flex-wrap gap-1">
            {settings?.providers.map((provider) => (
              <Button
                key={provider}
                variant={provider === keyProvider ? "default" : "outline"}
                size="sm"
                onClick={() => setKeyProvider(provider)}
              >
                {provider}
              </Button>
            ))}
          </div>

          <div className="mt-3 space-y-2">
            {keyRows.length === 0 && (
              <p className="text-xs text-muted-foreground">{t("llm.noKeys")}</p>
            )}
            {keyRows.map((entry) => (
              <div
                key={entry.fingerprint}
                className="flex items-center justify-between gap-2 rounded-lg border px-3 py-2"
              >
                <code className="text-xs">{entry.fingerprint}</code>
                <div className="flex items-center gap-2">
                  <Badge variant="secondary">
                    {entry.source === "admin"
                      ? t("llm.sourceAdmin")
                      : t("llm.sourceEnvironment")}
                  </Badge>
                  {entry.source === "admin" && (
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={busy}
                      onClick={() => removeKey(entry.fingerprint)}
                    >
                      {t("llm.removeKey")}
                    </Button>
                  )}
                </div>
              </div>
            ))}
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Input
              type="password"
              value={newKey}
              onChange={(event) => setNewKey(event.target.value)}
              placeholder={t("llm.keyPlaceholder")}
              className="h-9 min-w-56 flex-1"
              autoComplete="off"
            />
            <Button variant="secondary" onClick={addKey} disabled={busy}>
              {t("llm.addKey")}
            </Button>
          </div>
        </section>
      </div>

      <div className="px-5 py-5">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-sm font-medium">{t("llm.testTitle")}</h3>
          <Button
            variant="secondary"
            size="sm"
            onClick={runChain}
            disabled={!settings}
          >
            {t("llm.runChain")}
          </Button>
          {settings?.chain.map((model) => (
            <Button
              key={model}
              variant="outline"
              size="sm"
              onClick={() => testModel(model)}
              disabled={probeModel !== null}
            >
              {probeModel === model ? t("llm.testing") : t("llm.test")}
            </Button>
          ))}
        </div>

        {probe && (
          <div className="mt-3 overflow-x-auto rounded-lg border">
            <table className="w-full text-left text-xs">
              <thead className="bg-muted/40">
                <tr>
                  <th className="px-3 py-2">{t("llm.model")}</th>
                  <th className="px-3 py-2">{t("llm.key")}</th>
                  <th className="px-3 py-2">{t("llm.seconds")}</th>
                  <th className="px-3 py-2">{t("llm.result")}</th>
                </tr>
              </thead>
              <tbody>
                {(probe.attempts ?? []).map((attempt, index) => (
                  <tr key={`${attempt.model}-${index}`} className="border-t">
                    <td className="px-3 py-2 font-mono">{attempt.model}</td>
                    <td className="px-3 py-2 font-mono">
                      {attempt.key_fingerprint ?? "—"}
                    </td>
                    <td className="px-3 py-2">{attempt.seconds.toFixed(1)}</td>
                    <td className="px-3 py-2">
                      {attempt.ok ? (
                        <span className="text-emerald-600">{t("llm.ok")}</span>
                      ) : (
                        <span className="text-red-600">
                          {attempt.error || attempt.kind}
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {probe?.answered_by && (
          <p className="mt-2 text-xs text-muted-foreground">
            {t("llm.answeredBy")}: <code>{probe.answered_by}</code>
          </p>
        )}
        {message && <p className="mt-2 text-xs text-emerald-600">{message}</p>}
        {error && <p className="mt-2 text-xs text-red-600">{error}</p>}
      </div>
    </div>
  );
}
