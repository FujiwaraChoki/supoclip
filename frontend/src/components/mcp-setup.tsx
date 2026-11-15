"use client";

import { useRef } from "react";
import { useTranslations } from "next-intl";
import { Copy } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useCopyText } from "@/lib/copy-text";
import { GITHUB_URL, getMcpUrl } from "@/lib/site";

const STDIO_SOURCE = `git+${GITHUB_URL}#subdirectory=mcp`;

/** Copy-paste config for each kind of MCP client. Only the URL ever goes in a URL field. */
export function mcpSnippets(url: string, key: string) {
  return {
    claudeCode: `claude mcp add --transport http supoclip ${url} --header "Authorization: Bearer ${key}"`,
    json: JSON.stringify(
      { mcpServers: { supoclip: { url, headers: { Authorization: `Bearer ${key}` } } } },
      null,
      2,
    ),
    claudeDesktop: JSON.stringify(
      {
        mcpServers: {
          supoclip: {
            command: "npx",
            args: ["-y", "mcp-remote", url, "--header", "Authorization:${SUPOCLIP_AUTH}"],
            env: { SUPOCLIP_AUTH: `Bearer ${key}` },
          },
        },
      },
      null,
      2,
    ),
    stdio: `claude mcp add supoclip -e SUPOCLIP_API_KEY=${key} -- uvx --from "${STDIO_SOURCE}" supoclip-mcp`,
  };
}

export function Snippet({ label, value }: { label: string; value: string }) {
  const t = useTranslations("settings.apiKeys.mcp");
  const copyText = useCopyText();
  const ref = useRef<HTMLElement>(null);
  return (
    <div className="relative">
      <pre className="overflow-x-auto rounded-lg border bg-muted px-3 py-2.5 pr-12 text-xs leading-relaxed">
        <code ref={ref} tabIndex={-1} className="select-text whitespace-pre">{value}</code>
      </pre>
      <Button
        size="icon"
        variant="ghost"
        className="absolute right-1.5 top-1.5 size-7"
        onClick={() => void copyText(value, ref.current)}
        aria-label={t("copy", { label })}
      >
        <Copy className="size-3.5" />
      </Button>
    </div>
  );
}

/** Config for MCP clients that take a key, filled with a key that was just created. */
export function KeyClientSetup({ apiKey }: { apiKey: string }) {
  const t = useTranslations("settings.apiKeys.mcp");
  const snippets = mcpSnippets(getMcpUrl(), apiKey);
  const tabs = [
    { id: "json", label: t("tabs.json"), hint: t("hints.json"), value: snippets.json },
    { id: "stdio", label: t("tabs.stdio"), hint: t("hints.stdio"), value: snippets.stdio },
  ];

  return (
    <Tabs defaultValue="json">
      <TabsList className="w-full sm:w-fit">
        {tabs.map((tab) => (
          <TabsTrigger key={tab.id} value={tab.id}>{tab.label}</TabsTrigger>
        ))}
      </TabsList>
      {tabs.map((tab) => (
        <TabsContent key={tab.id} value={tab.id} className="min-w-0 space-y-2">
          <p className="text-xs text-muted-foreground">{tab.hint}</p>
          <Snippet label={tab.label} value={tab.value} />
        </TabsContent>
      ))}
    </Tabs>
  );
}
