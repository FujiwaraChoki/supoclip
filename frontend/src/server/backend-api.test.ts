import { createProxyResponse, fetchBackend } from "./backend-api";

describe("backend S3 redirects", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("returns signed redirects to the browser without following them with backend credentials", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 307, headers: {
      Location: "https://s3.test/private-clip?signature=test", "Cache-Control": "private, no-store",
    } }));
    vi.stubGlobal("fetch", fetchMock);
    const response = createProxyResponse(await fetchBackend("/tasks/task/clips/clip/file", { userId: "owner" }));
    expect(fetchMock).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ redirect: "manual" }));
    expect(response.status).toBe(307);
    expect(response.headers.get("location")).toBe("https://s3.test/private-clip?signature=test");
    expect(response.headers.get("cache-control")).toBe("private, no-store");
  });
});
