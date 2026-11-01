// Pure helpers for the puzzle-list activity sparkline (ActivitySparkline.tsx).

// Chat rate (messages per minute) that draws a full-height bar.
const FULL_RATE = 1.0;

/** Bar height 0–1 from a bar's chat count and length. Rate on a scale shared by
 * every puzzle, square-rooted so a quiet trickle is still visible. */
export function barHeight(chat: number, barMinutes: number): number {
  if (chat <= 0) return 0;
  if (barMinutes <= 0) return 1;
  return Math.min(1, Math.sqrt(chat / barMinutes / FULL_RATE));
}

/** The latest of several ISO timestamps, ignoring missing ones. */
export function latestActivity(
  ...stamps: (string | null | undefined)[]
): string | null {
  let best: string | null = null;
  let bestMs = -Infinity;
  for (const s of stamps) {
    if (!s) continue;
    const ms = Date.parse(s);
    if (ms > bestMs) {
      best = s;
      bestMs = ms;
    }
  }
  return best;
}
