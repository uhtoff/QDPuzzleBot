// Reconnect delay (ms) for the nth consecutive failed attempt (0-based):
// 1s, 2s, 4s ... capped at 60s.
export function backoffDelay(attempt, base = 1000, cap = 60000) {
  return Math.min(cap, base * 2 ** Math.max(0, attempt));
}

// A persisted attempt counter, defensively read.
export function resolveAttempt(stored) {
  return Number.isInteger(stored) && stored > 0 ? stored : 0;
}

// Delays at or above this use chrome.alarms (lets the worker sleep); shorter use setTimeout.
export const ALARM_MIN_MS = 30000;
export function reconnectVia(delayMs) {
  return delayMs >= ALARM_MIN_MS ? "alarm" : "timeout";
}
