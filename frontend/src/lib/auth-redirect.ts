/**
 * Where to go after signing in or up: a same-site `?next=/path`, else home.
 * Absolute and protocol-relative URLs are ignored so the param can't redirect off-site.
 */
export function postAuthPath(search: string): string {
  const next = new URLSearchParams(search).get("next");
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) return "/";
  return next;
}

/** Sign-in page that returns to `path` (pathname plus search) afterwards. */
export function signInPath(path: string): string {
  return `/sign-in?${new URLSearchParams({ next: path })}`;
}
