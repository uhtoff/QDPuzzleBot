/**
 * Reconnect policy for the shared activity websocket.
 *
 * Kept pure and separate from the socket so it can be tested: the socket layer
 * only asks "given this close code and how many times I have already tried,
 * how long do I wait?" and honours a null by giving up.
 */

/** Close codes that no amount of retrying can fix. 1008 is what the server
 *  sends for an unauthenticated session, a user no longer in any guild, and a
 *  cross-site handshake -- reconnecting just hammers it every few seconds. */
export const FATAL_CLOSE_CODES = [1008];

export const RECONNECT_BASE_MS = 3000;
export const RECONNECT_MAX_MS = 30000;

/**
 * Milliseconds to wait before the next attempt, or null to stop trying.
 * `attempt` is the number of failed attempts so far (0 for the first retry).
 */
export function reconnectDelay(code: number, attempt: number): number | null {
  if (FATAL_CLOSE_CODES.includes(code)) return null;
  const backoff = RECONNECT_BASE_MS * 2 ** Math.max(0, attempt);
  return Math.min(backoff, RECONNECT_MAX_MS);
}
