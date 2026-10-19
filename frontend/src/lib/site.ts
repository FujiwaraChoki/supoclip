export const SITE_NAME = "SupoClip";
export const DEFAULT_SITE_URL = "https://www.supoclip.com";
export const HOSTED_APP_URL = DEFAULT_SITE_URL;
export const GITHUB_URL = "https://github.com/FujiwaraChoki/supoclip";
export const APP_STORE_ID = "6784760040";
export const APP_STORE_URL = `https://apps.apple.com/us/app/supoclip/id${APP_STORE_ID}`;
export const PRODUCT_HUNT_URL =
  "https://www.producthunt.com/products/supoclip?embed=true&utm_source=badge-featured&utm_medium=badge&utm_campaign=badge-supoclip";
/** Product Hunt's launch badge; it shows the live upvote count once the launch is up. */
export const PRODUCT_HUNT_BADGE_URL =
  "https://api.producthunt.com/widgets/embed-image/v1/featured.svg?post_id=1276786&theme=dark";

/** Opens the App Store's offer-code redemption for a creator's custom code (the "Creator offer"). */
export function appStoreRedeemUrl(code: string) {
  return `https://apps.apple.com/redeem?ctx=offercodes&id=${APP_STORE_ID}&code=${encodeURIComponent(code.toUpperCase())}`;
}

export function getSiteUrl() {
  try {
    return new URL(process.env.NEXT_PUBLIC_APP_URL || DEFAULT_SITE_URL).origin;
  } catch {
    return DEFAULT_SITE_URL;
  }
}
