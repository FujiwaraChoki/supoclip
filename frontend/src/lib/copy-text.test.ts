import { toast } from "sonner";

import { copyText, useCopyText } from "./copy-text";
import { renderHook } from "@testing-library/react";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), message: vi.fn() } }));

function deniedClipboard() {
  const writeText = vi.fn().mockRejectedValue(new DOMException("Clipboard permission denied", "NotAllowedError"));
  vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
  return writeText;
}

afterEach(() => {
  vi.unstubAllGlobals();
  document.getElementById("supoclip-manual-copy")?.remove();
  vi.mocked(toast.success).mockReset();
  vi.mocked(toast.message).mockReset();
});

it("copies with the clipboard API", async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });

  await expect(copyText("sk_secret")).resolves.toBe("copied");
  expect(writeText).toHaveBeenCalledWith("sk_secret");
  expect(document.getElementById("supoclip-manual-copy")).toBeNull();
});

it("falls back to a hidden textarea when the clipboard rejects", async () => {
  deniedClipboard();
  const execCommand = vi.fn().mockReturnValue(true);
  document.execCommand = execCommand;

  await expect(copyText("sk_secret")).resolves.toBe("copied");

  expect(execCommand).toHaveBeenCalledWith("copy");
  expect(document.querySelector("textarea")).toBeNull();
  expect(document.getElementById("supoclip-manual-copy")).toBeNull();
});

it("selects the visible text when both copy methods fail", async () => {
  deniedClipboard();
  document.execCommand = vi.fn().mockReturnValue(false);
  const target = document.createElement("code");
  target.textContent = "sk_secret";
  document.body.appendChild(target);

  await expect(copyText("sk_secret", { target })).resolves.toBe("manual");

  expect(window.getSelection()?.toString()).toBe("sk_secret");
  expect(document.getElementById("supoclip-manual-copy")).toBeNull();
  target.remove();
});

it("shows a selected field when there is nothing on the page to select", async () => {
  deniedClipboard();
  document.execCommand = vi.fn().mockImplementation(() => {
    throw new Error("execCommand unsupported");
  });

  await expect(copyText("https://supoclip.com/share/abc", { manualLabel: "Copy by hand" })).resolves.toBe("manual");

  const field = document.getElementById("supoclip-manual-copy");
  expect(field).toBeInstanceOf(HTMLTextAreaElement);
  expect(field).toHaveValue("https://supoclip.com/share/abc");
  expect(field).toHaveAttribute("aria-label", "Copy by hand");
  expect(document.activeElement).toBe(field);
});

it("toasts success and the manual fallback", async () => {
  const writeText = vi.fn()
    .mockResolvedValueOnce(undefined)
    .mockRejectedValueOnce(new DOMException("denied", "NotAllowedError"));
  vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
  document.execCommand = vi.fn().mockReturnValue(false);
  const { result, unmount } = renderHook(() => useCopyText());

  await expect(result.current("sk_secret")).resolves.toBe("copied");
  expect(toast.success).toHaveBeenCalledWith("Copied to clipboard");

  await expect(result.current("sk_secret")).resolves.toBe("manual");
  expect(toast.message).toHaveBeenCalledWith("Clipboard is blocked. The text is selected so you can copy it manually.");
  expect(document.getElementById("supoclip-manual-copy")).not.toBeNull();

  unmount();
  expect(document.getElementById("supoclip-manual-copy")).toBeNull();
});
