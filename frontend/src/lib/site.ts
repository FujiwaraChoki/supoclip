export const SITE_NAME = "SupoClip";
export const DEFAULT_SITE_URL = "https://www.supoclip.com";
export const HOSTED_APP_URL = DEFAULT_SITE_URL;
export const GITHUB_URL = "https://github.com/FujiwaraChoki/supoclip";

export function getSiteUrl() {
  try {
    return new URL(process.env.NEXT_PUBLIC_APP_URL || DEFAULT_SITE_URL).origin;
  } catch {
    return DEFAULT_SITE_URL;
  }
}
