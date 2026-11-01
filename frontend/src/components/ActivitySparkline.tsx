import type { PuzzleActivity } from "../types";
import { barHeight } from "../lib/sparkline";

const WIDTH = 72;
const HEIGHT = 16;
const GAP = 1;
const EDIT_MIN_PX = 2;

function summary(a: PuzzleActivity): string {
  const messages = a.bars.reduce((n, b) => n + b.chat, 0);
  const edits = a.bars.filter((b) => b.edited).length;
  const hours = (a.bar_minutes * a.bars.length) / 60;
  const since = hours < 1 ? `${Math.round(hours * 60)}m` : `${Math.round(hours)}h`;
  const parts = [`${messages} message${messages === 1 ? "" : "s"}`];
  if (edits) parts.push(`sheet edited in ${edits} bar${edits === 1 ? "" : "s"}`);
  return `${parts.join(" · ")} · over ${since}`;
}

/** Whole-life activity: bar height is chat rate; orange bars had sheet edits. */
export function ActivitySparkline({ activity }: { activity: PuzzleActivity | null | undefined }) {
  if (!activity || activity.bars.length === 0) return null;
  const n = activity.bars.length;
  const barW = Math.max(1, (WIDTH - GAP * (n - 1)) / n);
  return (
    <svg
      width={WIDTH}
      height={HEIGHT}
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      role="img"
      aria-label={summary(activity)}
      style={{ flex: "none", display: "block" }}
    >
      <title>{summary(activity)}</title>
      <line
        x1={0}
        x2={WIDTH}
        y1={HEIGHT - 0.5}
        y2={HEIGHT - 0.5}
        stroke="var(--mantine-color-default-border)"
        strokeWidth={1}
      />
      {activity.bars.map((b, i) => {
        let h = barHeight(b.chat, activity.bar_minutes) * HEIGHT;
        if (b.edited) h = Math.max(h, EDIT_MIN_PX);
        if (h <= 0) return null;
        return (
          <rect
            key={i}
            x={i * (barW + GAP)}
            y={HEIGHT - h}
            width={barW}
            height={h}
            fill={b.edited ? "var(--mantine-color-orange-6)" : "var(--mantine-color-blue-5)"}
          />
        );
      })}
    </svg>
  );
}
