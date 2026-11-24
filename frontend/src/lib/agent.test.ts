import { answeredReply, firstLink, type AgentItem } from "./agent";

describe("agent helpers", () => {
  it("finds the video link in a message", () => {
    expect(firstLink("clip this https://youtu.be/abc please")).toBe("https://youtu.be/abc");
    expect(firstLink("no link here")).toBeNull();
  });

  it("treats the next message after an options card as its answer", () => {
    const items: AgentItem[] = [
      { type: "tool", id: "t1", name: "show_options", input: {}, status: "done" },
      { type: "text", id: "x", text: "Pick one" },
      { type: "user", id: "u1", text: "Make a series", attachments: [], created_at: null },
    ];
    expect(answeredReply(items, 0)).toBe("Make a series");
    expect(answeredReply(items.slice(0, 2), 0)).toBeNull();
  });
});
