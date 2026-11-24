import { render, screen } from "@testing-library/react";

import { AgentMarkdown } from "./markdown";

describe("AgentMarkdown", () => {
  it("renders an intro line followed by a list as a paragraph and a list", () => {
    const { container } = render(<AgentMarkdown text={"Here's the plan:\n- **Fill the gaps.** Two posts\n- Tidy clip 3"} />);
    expect(container.querySelector("p")?.textContent).toBe("Here's the plan:");
    const items = container.querySelectorAll("li");
    expect(items).toHaveLength(2);
    expect(items[0].querySelector("strong")?.textContent).toBe("Fill the gaps.");
  });

  it("links only to app paths and web pages, never scripts", () => {
    render(<AgentMarkdown text={"[Calendar](/calendar) [Site](https://example.com) [Bad](javascript:void0)"} />);
    expect(screen.getByText("Calendar").closest("a")?.getAttribute("href")).toBe("/calendar");
    expect(screen.getByText("Site").closest("a")?.getAttribute("href")).toBe("https://example.com");
    expect(screen.getByText("Bad").closest("a")).toBeNull();
  });

  it("never renders the agent's text as HTML", () => {
    const { container } = render(<AgentMarkdown text={"<img src=x onerror=alert(1)> hi"} />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain("<img src=x onerror=alert(1)> hi");
  });
});
