import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { server } from "@/test/setup";
import { CHAT_MESSAGE, ManualAnalysisPanel } from "./manual-analysis-panel";

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock("sonner", () => ({ toast }));
const triggerFileDownload = vi.hoisted(() => vi.fn());
vi.mock("@/lib/clip-export", () => ({ triggerFileDownload }));

const PROMPT = "# SupoClip clip selection\n\n[00:00 - 00:08] hello there";
const ANSWER = '{"most_relevant_segments":[],"summary":"s","key_topics":[]}';

function renderPanel(props: Partial<Parameters<typeof ManualAnalysisPanel>[0]> = {}) {
  const onSubmitted = vi.fn();
  render(<ManualAnalysisPanel taskId="1234567890" readOnly={false} onSubmitted={onSubmitted} {...props} />);
  return { onSubmitted };
}

function promptEndpoint(status = 200) {
  server.use(
    http.get("/api/tasks/1234567890/analysis-prompt", () =>
      status === 200 ? new HttpResponse(PROMPT, { headers: { "content-type": "text/markdown" } })
        : HttpResponse.json({ detail: "This task is not waiting for an AI analysis." }, { status }),
    ),
  );
}

beforeEach(() => {
  toast.success.mockClear();
  toast.error.mockClear();
  triggerFileDownload.mockClear();
  URL.createObjectURL = vi.fn(() => "blob:prompt");
  URL.revokeObjectURL = vi.fn();
});

describe("ManualAnalysisPanel", () => {
  it("downloads the prompt as a Markdown file", async () => {
    promptEndpoint();
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "Download prompt" }));
    await waitFor(() => expect(triggerFileDownload).toHaveBeenCalledWith("blob:prompt", "supoclip-prompt-12345678.md"));
    const blob = vi.mocked(URL.createObjectURL).mock.calls[0][0] as Blob;
    const text = await new Promise<string>((resolve) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.readAsText(blob);
    });
    expect(text).toBe(PROMPT);
    expect(toast.success).toHaveBeenCalledWith("Prompt downloaded");
  });

  it("tells the chat what to do with the attached file, and copies that message", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    renderPanel();
    expect(screen.getByText(CHAT_MESSAGE)).toBeInTheDocument();
    // The prompt itself is a file to attach: it is never copied.
    expect(screen.queryByRole("button", { name: "Copy prompt" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Copy message" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(CHAT_MESSAGE));
    expect(toast.success).toHaveBeenCalledWith("Message copied");
  });

  it("asks to copy by hand when the clipboard is refused", async () => {
    Object.defineProperty(navigator, "clipboard", { value: { writeText: vi.fn().mockRejectedValue(new Error("denied")) }, configurable: true });
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "Copy message" }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Could not copy. Select the message and copy it instead."));
  });

  it("reports a prompt that cannot be loaded", async () => {
    promptEndpoint(409);
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "Download prompt" }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("This task is not waiting for an AI analysis."));
    expect(triggerFileDownload).not.toHaveBeenCalled();
  });

  it("submits the pasted answer and refreshes the task", async () => {
    let body: unknown;
    server.use(http.post("/api/tasks/1234567890/analysis", async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({ message: "Analysis accepted", segments: 1 });
    }));
    const { onSubmitted } = renderPanel();
    const submit = screen.getByRole("button", { name: "Generate clips" });
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByRole("textbox", { name: "AI chat reply (JSON)" }), { target: { value: ANSWER } });
    expect(screen.getByText(`${ANSWER.length} characters`)).toBeInTheDocument();
    fireEvent.click(submit);
    await waitFor(() => expect(onSubmitted).toHaveBeenCalled());
    expect(body).toEqual({ response: ANSWER });
    expect(toast.success).toHaveBeenCalledWith("Answer accepted. Generating your clips…");
  });

  it("loads an uploaded file into the box so it can be checked before sending", async () => {
    renderPanel();
    const file = new File([ANSWER], "reply.json", { type: "application/json" });
    fireEvent.change(screen.getByTestId("manual-analysis-file"), { target: { files: [file] } });
    await waitFor(() => expect(screen.getByRole("textbox", { name: "AI chat reply (JSON)" })).toHaveValue(ANSWER));
    expect(screen.getByText("Loaded reply.json")).toBeInTheDocument();
  });

  it("refuses a file too large to be an analysis", async () => {
    renderPanel();
    const file = new File(["x".repeat(500_001)], "big.json");
    fireEvent.change(screen.getByTestId("manual-analysis-file"), { target: { files: [file] } });
    expect(await screen.findByRole("alert")).toHaveTextContent("That file is too large to be a clip analysis.");
    expect(screen.getByRole("textbox", { name: "AI chat reply (JSON)" })).toHaveValue("");
  });

  it("shows the reason an answer was rejected and keeps the text for another try", async () => {
    server.use(http.post("/api/tasks/1234567890/analysis", () =>
      HttpResponse.json({ detail: "The JSON looks cut off (it never closes). Ask the chat to resend the full answer." }, { status: 422 })));
    const { onSubmitted } = renderPanel();
    fireEvent.change(screen.getByRole("textbox", { name: "AI chat reply (JSON)" }), { target: { value: "{ cut" } });
    fireEvent.click(screen.getByRole("button", { name: "Generate clips" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The JSON looks cut off");
    expect(screen.getByRole("textbox", { name: "AI chat reply (JSON)" })).toHaveValue("{ cut");
    expect(onSubmitted).not.toHaveBeenCalled();
    // Editing clears the stale error.
    fireEvent.change(screen.getByRole("textbox", { name: "AI chat reply (JSON)" }), { target: { value: ANSWER } });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("refreshes instead of failing when the task already moved on", async () => {
    server.use(http.post("/api/tasks/1234567890/analysis", () =>
      HttpResponse.json({ detail: "This task is not waiting for an AI analysis." }, { status: 409 })));
    const { onSubmitted } = renderPanel();
    fireEvent.change(screen.getByRole("textbox", { name: "AI chat reply (JSON)" }), { target: { value: ANSWER } });
    fireEvent.click(screen.getByRole("button", { name: "Generate clips" }));
    await waitFor(() => expect(onSubmitted).toHaveBeenCalled());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("offers nothing to someone who only views the task", () => {
    renderPanel({ readOnly: true });
    expect(screen.getByText("Only the owner of this task can add the AI answer.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Download prompt" })).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });
});
