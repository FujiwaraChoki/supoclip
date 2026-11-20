import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { draft } from "@/lib/editor/document.test-fixture";
import { draftCacheKey, serializeDraft } from "@/lib/editor/draft-cache";
import { BrowserBackupsDialog, RebuildDialog, SourceIncompleteAlert } from "./editor-recovery";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

it("explains a truncated editing copy and offers a rebuild", () => {
  const onRebuild = vi.fn();
  render(<SourceIncompleteAlert duration={9.07} expected={36.42} onRebuild={onRebuild} />);
  expect(screen.getByTestId("source-incomplete")).toHaveTextContent("only 9 sec long instead of 36 sec");
  fireEvent.click(screen.getByRole("button", { name: /rebuild editing copy/i }));
  expect(onRebuild).toHaveBeenCalled();
});

it("says what happens to the current edits before rebuilding", async () => {
  const onRebuild = vi.fn(async () => {});
  const onOpenChange = vi.fn();
  render(<RebuildDialog open onOpenChange={onOpenChange} onRebuild={onRebuild} />);
  expect(screen.getByText(/don't carry over/i)).toBeInTheDocument();
  expect(screen.getByText(/Download previous draft/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Rebuild" }));
  await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
  expect(onRebuild).toHaveBeenCalledOnce();
});

it("frees saved copies and keeps unsaved changes", async () => {
  const saved = draftCacheKey("/api/tasks/t/clips/saved/editor");
  const unsaved = draftCacheKey("/api/tasks/t/clips/unsaved/editor");
  localStorage.setItem(saved, JSON.stringify({
    basis: "clip.mp4", revision: 1, saved: JSON.stringify(draft),
    history: { past: Array.from({ length: 50 }, () => draft), present: draft, future: [] },
  }));
  const pending = serializeDraft("clip.mp4", 1, { past: [], present: { ...draft, volume: 0.2 }, future: [] });
  localStorage.setItem(unsaved, pending);

  render(<BrowserBackupsDialog open onOpenChange={() => {}} />);
  expect(screen.getByTestId("browser-backups-summary")).toHaveTextContent("2 clips have a browser backup");
  expect(screen.getByTestId("browser-backups-summary")).toHaveTextContent("1 has changes that aren't in your account yet");
  expect(screen.getByText(/Deleting projects doesn't change this storage/)).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Remove saved copies" }));

  await waitFor(() =>
    expect(screen.getByTestId("browser-backups-summary")).toHaveTextContent("1 clip has a browser backup"),
  );
  expect(localStorage.getItem(saved)).toBeNull();
  expect(localStorage.getItem(unsaved)).toBe(pending);
  expect(screen.getByRole("button", { name: "Remove saved copies" })).toBeDisabled();
});
