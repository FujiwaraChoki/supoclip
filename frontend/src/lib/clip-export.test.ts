import { clipExportFilePath, clipExportPollDelayMs, pollClipExport, triggerFileDownload } from "./clip-export";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("clip export polling", () => {
  it("backs off and returns once the job succeeds", async () => {
    const sleeps: number[] = [];
    const calls: string[] = [];
    const job = await pollClipExport({
      start: async () => {
        calls.push("start");
        return jsonResponse({ id: "export-1", status: "queued", progress: 0, preset: "tiktok", error: null, download_url: null });
      },
      status: async (id) => {
        calls.push(id);
        if (calls.length === 2) {
          return jsonResponse({ id, status: "running", progress: 40, preset: "tiktok", error: null, download_url: null });
        }
        return jsonResponse({
          id,
          status: "succeeded",
          progress: 100,
          preset: "tiktok",
          error: null,
          download_url: "/tasks/task/clips/clip/exports/export-1/file",
        });
      },
      readError: async () => "failed",
      fallbackMessage: "Could not export",
      timeoutMessage: "Timed out",
      sleep: async (ms) => {
        sleeps.push(ms);
      },
      now: () => 0,
    });

    expect(job.status).toBe("succeeded");
    expect(calls).toEqual(["start", "export-1", "export-1"]);
    expect(sleeps).toEqual([clipExportPollDelayMs(0), clipExportPollDelayMs(1)]);
    expect(sleeps[0]).toBeLessThan(sleeps[1]);
  });

  it("stops when the job fails and when polling runs too long", async () => {
    await expect(pollClipExport({
      start: async () => jsonResponse({ id: "export-2", status: "failed", progress: 0, preset: "reels", error: "Export failed. Try again.", download_url: null }),
      status: async () => jsonResponse({}),
      readError: async (response) => `status ${response.status}`,
      fallbackMessage: "Could not export",
      timeoutMessage: "Timed out",
      sleep: async () => undefined,
    })).rejects.toThrow("Export failed. Try again.");

    let clock = 0;
    await expect(pollClipExport({
      start: async () => jsonResponse({ id: "export-3", status: "running", progress: 1, preset: "shorts", error: null, download_url: null }),
      status: async () => jsonResponse({ id: "export-3", status: "running", progress: 1, preset: "shorts", error: null, download_url: null }),
      readError: async () => "failed",
      fallbackMessage: "Could not export",
      timeoutMessage: "Timed out",
      sleep: async () => {
        clock += 60_000;
      },
      now: () => clock,
      maxWaitMs: 1_000,
    })).rejects.toThrow("Timed out");
  });

  it("surfaces a start error and builds the download path", async () => {
    await expect(pollClipExport({
      start: async () => jsonResponse({ detail: "nope" }, 400),
      status: async () => jsonResponse({}),
      readError: async (response) => `status ${response.status}`,
      fallbackMessage: "Could not export",
      timeoutMessage: "Timed out",
    })).rejects.toThrow("status 400");

    expect(clipExportFilePath("/api/tasks", "task", "clip", "export")).toBe(
      "/api/tasks/task/clips/clip/exports/export/file",
    );
  });

  it("clicks a download link with the requested filename", () => {
    const clicked: string[] = [];
    const original = HTMLAnchorElement.prototype.click;
    HTMLAnchorElement.prototype.click = function click() {
      clicked.push(`${this.download}|${this.getAttribute("href")}`);
    };
    try {
      triggerFileDownload("/api/tasks/task/clips/clip/exports/export/file", "clip_tiktok.mp4");
    } finally {
      HTMLAnchorElement.prototype.click = original;
    }
    expect(clicked).toEqual(["clip_tiktok.mp4|/api/tasks/task/clips/clip/exports/export/file"]);
  });
});
