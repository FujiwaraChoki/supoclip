type AuthOriginEnvironment = {
  NEXT_PUBLIC_APP_URL?: string;
  BETTER_AUTH_URL?: string;
  BETTER_AUTH_TRUSTED_ORIGINS?: string;
  NEXT_PUBLIC_SELF_HOST?: string;
};

function toOrigin(value?: string): string | null {
  if (!value?.trim()) return null;
  try {
    const url = new URL(value.trim());
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.hostname.includes("*")) return null;
    return url.origin;
  } catch {
    return null;
  }
}

export function getAuthTrustedOrigins(env: AuthOriginEnvironment): string[] {
  const origins = [
    toOrigin(env.NEXT_PUBLIC_APP_URL),
    toOrigin(env.BETTER_AUTH_URL),
    // Retain the existing origins for compatibility with hosted deployments.
    "http://localhost:3107",
    "http://sp.localhost:3107",
    "http://supoclip.localhost:3107",
    ...(env.BETTER_AUTH_TRUSTED_ORIGINS ?? "").split(",").map(toOrigin),
  ];
  const selfHost = !["false", "0", "no"].includes((env.NEXT_PUBLIC_SELF_HOST ?? "true").toLowerCase());
  if (selfHost) {
    // Compose used 3001 before the new template selected 3107.
    origins.push("http://localhost:3001", "http://127.0.0.1:3001", "http://127.0.0.1:3107");
  }
  return [...new Set(origins.filter((origin): origin is string => Boolean(origin)))];
}
