/**
 * Chat assistants that can add the hosted SupoClip MCP server as a connector.
 *
 * Every link here comes from the provider's own documentation (checked 2026-11-15) and opens
 * the provider's own connector page. None of them can prefill a server URL, so nothing about
 * the user ever goes into one: the user pastes the MCP URL, then signs in on supoclip.com
 * (OAuth), which creates a revocable key named after the app. No key is copied by hand.
 */
export type AssistantId = "claude" | "chatgpt" | "grok";

export type AssistantConnector = {
  id: AssistantId;
  name: string;
  /** The provider's page where a custom connector is added, as linked from its docs. */
  openUrl: string;
  /** The provider's official setup guide. */
  docsUrl: string;
  /** Number of steps under `settings.apiKeys.assistants.<id>.steps`. */
  steps: number;
  /** The step that pastes the server URL; the copyable URL sits under it. */
  urlStep: number;
};

export const ASSISTANT_CONNECTORS: readonly AssistantConnector[] = [
  {
    id: "claude",
    name: "Claude",
    openUrl: "https://claude.ai/customize/connectors",
    docsUrl: "https://claude.com/docs/connectors/custom/add-unlisted",
    steps: 4,
    urlStep: 2,
  },
  {
    id: "chatgpt",
    name: "ChatGPT",
    openUrl: "https://chatgpt.com/plugins",
    docsUrl: "https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt",
    steps: 4,
    urlStep: 2,
  },
  {
    id: "grok",
    name: "Grok",
    openUrl: "https://grok.com/connectors",
    docsUrl: "https://docs.x.ai/grok/connectors",
    steps: 3,
    urlStep: 2,
  },
];

/** Claude Code signs in with the same OAuth flow when no Authorization header is configured. */
export function claudeCodeOAuthCommands(url: string) {
  return `claude mcp add --transport http supoclip ${url}\nclaude mcp login supoclip`;
}
