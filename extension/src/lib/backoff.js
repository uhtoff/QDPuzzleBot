// Reconnect delay (ms) for the nth consecutive failed attempt (0-based):
// 1s, 2s, 4s ... capped at 60s.
export function backoffDelay(attempt, base = 1000, cap = 60000) {
  return Math.min(cap, base * 2 ** Math.max(0, attempt));
}
