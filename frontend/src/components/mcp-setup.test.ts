import { expect, it } from "vitest";

import { mcpSnippets } from "./mcp-setup";

it("keeps the key out of the URL in every snippet", () => {
  const url = "https://mcp.supoclip.com/mcp";
  const snippets = mcpSnippets(url, "sk_test");
  expect(snippets.claudeCode).toBe(
    'claude mcp add --transport http supoclip https://mcp.supoclip.com/mcp --header "Authorization: Bearer sk_test"',
  );
  expect(JSON.parse(snippets.json).mcpServers.supoclip).toEqual({
    url,
    headers: { Authorization: "Bearer sk_test" },
  });
  const desktop = JSON.parse(snippets.claudeDesktop).mcpServers.supoclip;
  expect(desktop.args).toContain(url);
  expect(desktop.env.SUPOCLIP_AUTH).toBe("Bearer sk_test");
  expect(snippets.stdio).toContain("SUPOCLIP_API_KEY=sk_test");
});
