import { clipArchivePath, downloadAllClips } from "./clip-archive";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("download all clips", () => {
  it("checks first, then hands the streamed ZIP to the browser", async () => {
    const requests: string[] = [];
    const triggered: string[] = [];

    const result = await downloadAllClips({
      taskApiUrl: "/api/tasks",
      taskId: "task-1",
      readError: async () => "failed",
      fallbackMessage: "Could not download",
      request: (async (input: RequestInfo | URL) => {
        requests.push(String(input));
        return jsonResponse({ filename: "morning-routine-clips.zip", clip_count: 3, total_bytes: 10 });
      }) as typeof fetch,
      trigger: (href, filename) => triggered.push(`${filename}|${href}`),
    });

    expect(result).toEqual({ filename: "morning-routine-clips.zip", clipCount: 3 });
    expect(requests).toEqual(["/api/tasks/task-1/clips/zip/preflight"]);
    expect(triggered).toEqual(["morning-routine-clips.zip|/api/tasks/task-1/clips/zip"]);
  });

  it("surfaces backend errors without starting a download", async () => {
    const trigger = vi.fn();

    await expect(downloadAllClips({
      taskApiUrl: "/api/tasks",
      taskId: "task-1",
      readError: async (response) => `status ${response.status}`,
      fallbackMessage: "Could not download",
      request: (async () => jsonResponse({ detail: "already running" }, 429)) as typeof fetch,
      trigger,
    })).rejects.toThrow("status 429");

    await expect(downloadAllClips({
      taskApiUrl: "/api/tasks",
      taskId: "task-1",
      readError: async () => "failed",
      fallbackMessage: "Could not download",
      request: (async () => new Response("<html>bad gateway</html>", { status: 200 })) as typeof fetch,
      trigger,
    })).rejects.toThrow("Could not download");

    expect(trigger).not.toHaveBeenCalled();
  });

  it("encodes the task id in the archive path", () => {
    expect(clipArchivePath("/api/tasks", "a/b")).toBe("/api/tasks/a%2Fb/clips/zip");
  });
});
