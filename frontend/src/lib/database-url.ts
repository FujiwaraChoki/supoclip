/** Bound each web process independently of the host's CPU count. */
export function databaseUrlWithPoolLimits(value: string | undefined): string | undefined {
  if (!value) return value;
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new Error("Invalid DATABASE_URL");
  }
  for (const [name, fallback] of [["connection_limit", "5"], ["pool_timeout", "10"]]) {
    const setting = url.searchParams.get(name) ?? fallback;
    if (!/^[1-9]\d*$/.test(setting) || !Number.isSafeInteger(Number(setting))) {
      throw new Error(`DATABASE_URL ${name} must be a positive integer`);
    }
    url.searchParams.set(name, setting);
  }
  return url.toString();
}
