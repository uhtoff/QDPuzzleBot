/**
 * Scheme allowlist for hrefs that came from outside the dashboard.
 *
 * Embed titles, embed urls and attachment urls in the chat viewer are relayed
 * from Discord message content, which any member (or any webhook/bot in the
 * channel) can set. React escapes text but does NOT sanitise an href, so a
 * `javascript:` url rendered into an anchor executes on click, with the
 * dashboard's session.
 */
const ALLOWED_PROTOCOLS = ["http:", "https:"];

/** Base for resolving relative urls (attachment paths on our own origin). Only
 *  the resulting *protocol* is inspected, never the host, so a fixed base keeps
 *  this pure -- it does not need to know where the dashboard is served from. */
const RELATIVE_BASE = "https://dashboard.invalid";

/**
 * The url if it is safe to put in an href, otherwise undefined (render it as
 * plain text rather than a link).
 */
export function safeHref(url: string | null | undefined): string | undefined {
  if (!url) return undefined;
  let parsed: URL;
  try {
    parsed = new URL(url, RELATIVE_BASE);
  } catch {
    return undefined;
  }
  return ALLOWED_PROTOCOLS.includes(parsed.protocol) ? url : undefined;
}
