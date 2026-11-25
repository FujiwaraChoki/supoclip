/**
 * A same-site path to come back to after checkout ("/tasks/abc?x=1"), or null. Only plain paths on
 * this site: never another host ("//evil.example", "https://…"), backslashes or control characters.
 */
export function safeReturnPath(raw: unknown): string | null {
  if (typeof raw !== "string" || raw.length > 300) return null;
  if (!raw.startsWith("/") || raw.startsWith("//") || raw.startsWith("/\\")) return null;
  if (/[\\\u0000-\u001f\u007f]/.test(raw)) return null;
  try {
    const parsed = new URL(raw, "https://supoclip.invalid");
    if (parsed.origin !== "https://supoclip.invalid") return null;
    // "/.//evil.example" and "/a/..//evil.example" resolve to a protocol-relative path.
    if (!parsed.pathname.startsWith("/") || parsed.pathname.startsWith("//")) return null;
    return `${parsed.pathname}${parsed.search}`;
  } catch {
    return null;
  }
}

/** `path` with `key=value` added to its query. */
export function withQuery(path: string, key: string, value: string): string {
  return `${path}${path.includes("?") ? "&" : "?"}${encodeURIComponent(key)}=${encodeURIComponent(value)}`;
}
