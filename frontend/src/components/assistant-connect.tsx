"use client";

import type { ReactNode } from "react";
import { useTranslations } from "next-intl";
import { ArrowUpRight, BookOpen, Info } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Snippet } from "@/components/mcp-setup";
import { ASSISTANT_CONNECTORS, claudeCodeOAuthCommands } from "@/lib/assistant-connectors";
import { getMcpUrl } from "@/lib/site";

const bold = (chunks: ReactNode) => <strong className="font-medium text-foreground">{chunks}</strong>;

/**
 * Connect Claude, ChatGPT or Grok with OAuth: the user pastes the server URL in the
 * assistant and signs in on supoclip.com. Takes no key, so none can end up in a link.
 */
export function AssistantConnect() {
  const t = useTranslations("settings.apiKeys.assistants");
  const url = getMcpUrl();

  return (
    <section data-tour="assistant-connect" className="mb-8 rounded-lg border p-4" aria-labelledby="assistant-connect-title">
      <h2 id="assistant-connect-title" className="text-sm font-semibold">{t("title")}</h2>
      <p className="mt-1 text-xs text-muted-foreground">{t("intro")}</p>

      <div className="mt-4">
        <p className="mb-1 text-xs font-medium text-muted-foreground">{t("urlLabel")}</p>
        <Snippet label={t("urlLabel")} value={url} />
      </div>

      <Tabs defaultValue={ASSISTANT_CONNECTORS[0].id} className="mt-4">
        <TabsList className="h-auto w-full sm:w-fit" aria-label={t("pick")}>
          {ASSISTANT_CONNECTORS.map((assistant) => (
            <TabsTrigger key={assistant.id} value={assistant.id}>{assistant.name}</TabsTrigger>
          ))}
        </TabsList>
        {ASSISTANT_CONNECTORS.map((assistant) => (
          <TabsContent key={assistant.id} value={assistant.id} className="space-y-3">
            <ol className="list-decimal space-y-1.5 pl-5 text-sm text-muted-foreground">
              {Array.from({ length: assistant.steps }, (_, index) => (
                <li key={index}>{t.rich(`${assistant.id}.steps.${index + 1}`, { b: bold })}</li>
              ))}
            </ol>

            <p className="flex gap-2 rounded-md bg-muted px-3 py-2 text-xs text-muted-foreground">
              <Info className="mt-0.5 size-3.5 shrink-0" aria-hidden />
              <span>{t(`${assistant.id}.availability`)}</span>
            </p>

            <div className="flex flex-wrap gap-2">
              <Button asChild size="sm">
                <a href={assistant.openUrl} target="_blank" rel="noopener noreferrer">
                  {t("open", { name: assistant.name })}
                  <ArrowUpRight className="size-3.5" aria-hidden />
                  <span className="sr-only">{t("newTab")}</span>
                </a>
              </Button>
              <Button asChild size="sm" variant="outline">
                <a href={assistant.docsUrl} target="_blank" rel="noopener noreferrer">
                  <BookOpen className="size-3.5" aria-hidden />
                  {t("guide", { name: assistant.name })}
                  <span className="sr-only">{t("newTab")}</span>
                </a>
              </Button>
            </div>

            {assistant.id === "claude" && (
              <div>
                <p className="mb-1 text-xs text-muted-foreground">{t("claude.code")}</p>
                <Snippet label={t("claude.codeLabel")} value={claudeCodeOAuthCommands(url)} />
              </div>
            )}
          </TabsContent>
        ))}
      </Tabs>

      <p className="mt-3 text-xs text-muted-foreground">{t("revoke")}</p>
    </section>
  );
}
