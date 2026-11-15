"use client";

import type { ReactNode } from "react";
import { useTranslations } from "next-intl";
import { Tabs as TabsPrimitive } from "radix-ui";
import { ArrowUpRight, Info } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Snippet } from "@/components/mcp-setup";
import { AssistantIcon } from "@/components/assistant-icon";
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
    <section
      className="mb-8 overflow-hidden rounded-xl border bg-card"
      aria-labelledby="assistant-connect-title"
    >
      <TabsPrimitive.Root defaultValue={ASSISTANT_CONNECTORS[0].id}>
        <div className="space-y-4 p-5">
          <div>
            <h2 id="assistant-connect-title" className="text-sm font-semibold">{t("title")}</h2>
            <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{t("intro")}</p>
          </div>

          <TabsPrimitive.List aria-label={t("pick")} className="grid grid-cols-3 gap-2">
            {ASSISTANT_CONNECTORS.map((assistant) => (
              <TabsPrimitive.Trigger
                key={assistant.id}
                value={assistant.id}
                className="flex items-center justify-center gap-2.5 rounded-lg border bg-background px-3 py-2.5 text-sm font-medium text-muted-foreground transition-colors outline-none hover:border-foreground/20 hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50 data-[state=active]:border-foreground data-[state=active]:text-foreground data-[state=active]:shadow-xs sm:justify-start"
              >
                <AssistantIcon assistant={assistant.id} className="size-5" />
                {assistant.name}
              </TabsPrimitive.Trigger>
            ))}
          </TabsPrimitive.List>
        </div>

        {ASSISTANT_CONNECTORS.map((assistant) => (
          <TabsPrimitive.Content
            key={assistant.id}
            value={assistant.id}
            className="border-t bg-muted/30 p-5 outline-none"
          >
            <ol className="space-y-3">
              {Array.from({ length: assistant.steps }, (_, index) => (
                <li key={index} className="flex gap-3">
                  <span
                    aria-hidden
                    className="flex size-5 shrink-0 items-center justify-center rounded-full border bg-background text-[11px] font-medium tabular-nums text-muted-foreground"
                  >
                    {index + 1}
                  </span>
                  <div className="min-w-0 flex-1 space-y-2 pt-px text-sm text-muted-foreground">
                    <p>{t.rich(`${assistant.id}.steps.${index + 1}`, { b: bold })}</p>
                    {index + 1 === assistant.urlStep && <Snippet label={t("urlLabel")} value={url} />}
                  </div>
                </li>
              ))}
            </ol>

            <div className="mt-5 flex flex-wrap items-center gap-x-4 gap-y-2 pl-8">
              <Button asChild size="sm">
                <a href={assistant.openUrl} target="_blank" rel="noopener noreferrer">
                  <AssistantIcon assistant={assistant.id} />
                  {t("open", { name: assistant.name })}
                  <ArrowUpRight className="size-3.5 opacity-60" aria-hidden />
                  <span className="sr-only">{t("newTab")}</span>
                </a>
              </Button>
              <a
                href={assistant.docsUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="text-xs text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
              >
                {t("guide", { name: assistant.name })}
                <span className="sr-only">{t("newTab")}</span>
              </a>
            </div>

            <p className="mt-4 flex gap-2 pl-8 text-xs leading-relaxed text-muted-foreground">
              <Info className="mt-0.5 size-3.5 shrink-0 opacity-70" aria-hidden />
              <span>{t(`${assistant.id}.availability`)}</span>
            </p>

            {assistant.id === "claude" && (
              <div className="mt-5 space-y-2 border-t pt-4">
                <p className="text-xs text-muted-foreground">{t("claude.code")}</p>
                <Snippet label={t("claude.codeLabel")} value={claudeCodeOAuthCommands(url)} />
              </div>
            )}
          </TabsPrimitive.Content>
        ))}
      </TabsPrimitive.Root>

      <p className="border-t px-5 py-3 text-xs leading-relaxed text-muted-foreground">{t("revoke")}</p>
    </section>
  );
}
