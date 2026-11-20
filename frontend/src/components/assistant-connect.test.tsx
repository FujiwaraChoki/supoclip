import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AssistantConnect } from "./assistant-connect";
import { ASSISTANT_CONNECTORS, claudeCodeOAuthCommands } from "@/lib/assistant-connectors";
import { HOSTED_MCP_URL } from "@/lib/site";

const OFFICIAL_HOSTS: Record<string, string[]> = {
  claude: ["claude.ai", "claude.com"],
  chatgpt: ["chatgpt.com", "help.openai.com"],
  grok: ["grok.com", "docs.x.ai"],
};

describe("assistant connectors", () => {
  it("only links to the provider's own pages, with nothing in the URL", () => {
    expect(ASSISTANT_CONNECTORS.map((assistant) => assistant.id)).toEqual(["claude", "chatgpt", "grok"]);
    for (const assistant of ASSISTANT_CONNECTORS) {
      for (const href of [assistant.openUrl, assistant.docsUrl]) {
        const url = new URL(href);
        expect(url.protocol).toBe("https:");
        expect(OFFICIAL_HOSTS[assistant.id]).toContain(url.hostname);
        expect(url.search).toBe("");
        expect(url.hash).toBe("");
      }
    }
  });

  it("has every step in every language", () => {
    const root = join(process.cwd(), "messages");
    for (const locale of readdirSync(root)) {
      const messages = JSON.parse(readFileSync(join(root, locale, "settings.json"), "utf8")).apiKeys.assistants;
      for (const assistant of ASSISTANT_CONNECTORS) {
        const steps = messages[assistant.id].steps;
        expect(Object.keys(steps), `${locale} ${assistant.id}`).toHaveLength(assistant.steps);
        expect(messages[assistant.id].availability, `${locale} ${assistant.id}`).toBeTruthy();
        // Connectors sign in with OAuth: no step may ask for a key.
        for (const step of Object.values<string>(steps)) expect(step).not.toMatch(/sk_|Bearer/);
      }
    }
  });

  it("signs Claude Code in with OAuth instead of a pasted key", () => {
    expect(claudeCodeOAuthCommands(HOSTED_MCP_URL)).toBe(
      "claude mcp add --transport http supoclip https://mcp.supoclip.com/mcp\nclaude mcp login supoclip",
    );
  });
});

describe("AssistantConnect", () => {
  it("shows each assistant's steps and opens its connector page in a new tab", () => {
    render(<AssistantConnect />);
    const section = screen.getByRole("region", { name: "Connect Claude, ChatGPT or Grok" });
    expect(within(section).getAllByText(HOSTED_MCP_URL).length).toBeGreaterThan(0);

    const open = screen.getByRole("link", { name: /Open Claude/ });
    expect(open).toHaveAttribute("href", "https://claude.ai/customize/connectors");
    expect(open).toHaveAttribute("target", "_blank");
    expect(open).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getByText(/Register automatically/)).toBeInTheDocument();

    for (const name of ["ChatGPT", "Grok"]) {
      const tab = screen.getByRole("tab", { name });
      fireEvent.mouseDown(tab);
      fireEvent.click(tab);
    }
    expect(screen.getByRole("link", { name: /Open Grok/ })).toHaveAttribute("href", "https://grok.com/connectors");
    expect(screen.getByText(/not confirmed for Grok/)).toBeInTheDocument();
  });

  it("never puts a key in any link", () => {
    render(<AssistantConnect />);
    for (const link of screen.getAllByRole("link")) {
      expect(link.getAttribute("href")).not.toMatch(/sk_|key=|token=/i);
    }
  });
});
