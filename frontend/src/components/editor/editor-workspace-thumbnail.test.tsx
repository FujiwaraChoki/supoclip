import { act, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { TooltipProvider } from "@/components/ui/tooltip";
import { EditorWorkspace } from "./editor-workspace";

vi.mock("next/link", () => ({ default: ({ children }: { children: React.ReactNode }) => children }));

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const clip = (id: string) => ({ id, filename: `${id}.mp4`, clip_order: 1, duration: 30, text: "", video_url: "/x" });

it("shows a placeholder for the open clip until its preview is prepared", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.stubGlobal("localStorage", { getItem: () => null, setItem: () => {} });
  let status = "unprepared";
  const fetch = vi.fn(async (url: string, init: RequestInit) => {
    if (url.endsWith("/prepare")) {
      status = "preparing";
      return Response.json({ status });
    }
    if (url === "/api/tasks/t/clips/a/editor" && init.method === "GET")
      return Response.json({ status, error: status === "failed" ? "Source missing" : undefined });
    throw new Error(`unexpected ${init.method} ${url}`);
  });
  vi.stubGlobal("fetch", fetch);
  render(
    <TooltipProvider>
      <EditorWorkspace taskId="t" title="Talk" clips={[clip("a"), clip("b")]} clip={clip("a")} onSelect={() => {}} refreshClips={async () => {}} />
    </TooltipProvider>,
  );
  const thumbnail = () => screen.getByRole("button", { name: /switch clip/i }).querySelector("span[aria-hidden]") as HTMLElement;
  await act(() => vi.advanceTimersByTimeAsync(0));
  expect(thumbnail().style.backgroundImage).toBe("");
  await act(() => vi.advanceTimersByTimeAsync(1600));
  expect(thumbnail().dataset.thumbnail).toBe("preparing");
  expect(thumbnail().style.backgroundImage).toBe("");
  // A failed preparation keeps the placeholder and the editor still reports the error.
  status = "failed";
  await act(() => vi.advanceTimersByTimeAsync(1600));
  expect(thumbnail().dataset.thumbnail).toBe("failed");
  expect(thumbnail().style.backgroundImage).toBe("");
  expect(screen.getAllByText(/Source missing|preparation failed|could not/i).length).toBeGreaterThan(0);
  // The picker is closed, so no sibling clip was looked at.
  expect(fetch.mock.calls.some(([url]) => url.includes("/clips/b/"))).toBe(false);
});
