// A base that no real link resolves to: a `next` value stays on it only when it is a plain path.
const SAME_SITE = "https://app.invalid";

/** `next` as a same-site path (pathname, search and hash), or null when it could leave the site. */
function sameSitePath(next: string | null | undefined): string | null {
  // Browsers drop tabs and newlines and read "\" as "/", so "/\t/evil.example" would
  // navigate to "//evil.example". Refuse those instead of guessing how each one parses.
  if (!next || !next.startsWith("/") || /[\u0000-\u001f\u007f\\]/.test(next)) return null;
  let url: URL;
  try {
    url = new URL(next, SAME_SITE);
  } catch {
    return null;
  }
  // "/..//evil.example" stays on the base but normalizes to "//evil.example".
  if (url.origin !== SAME_SITE || url.pathname.startsWith("//")) return null;
  return `${url.pathname}${url.search}${url.hash}`;
}

/**
 * Where to go after signing in or up: a same-site `?next=/path`, else home.
 * Absolute and protocol-relative URLs are ignored so the param can't redirect off-site.
 */
export function postAuthPath(search: string): string {
  return sameSitePath(new URLSearchParams(search).get("next")) ?? "/";
}

/** Sign-in page that returns to `path` (pathname plus search) afterwards. */
export function signInPath(path: string): string {
  return `/sign-in?${new URLSearchParams({ next: path })}`;
}

/**
 * Link from one auth page to the other (`/sign-in` or `/sign-up`) that keeps where the
 * visitor was headed and the email they were invited with.
 */
export function authPagePath(page: "/sign-in" | "/sign-up", params: { next?: string | string[]; email?: string | string[] }): string {
  const first = (value: string | string[] | undefined) => (Array.isArray(value) ? value[0] : value);
  const query = new URLSearchParams();
  const next = sameSitePath(first(params.next));
  const email = first(params.email)?.trim();
  if (next && next !== "/") query.set("next", next);
  if (email) query.set("email", email);
  const suffix = query.toString();
  return suffix ? `${page}?${suffix}` : page;
}
