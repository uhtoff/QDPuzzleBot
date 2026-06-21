export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  const diffSec = Math.round((Date.now() - then) / 1000);
  if (diffSec < 60) return "just now";
  const mins = Math.round(diffSec / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return `${days}d ago`;
}

/** Length of an answer counting only letters and digits (no spaces or punctuation). */
export function answerLength(solution: string | null): number {
  if (!solution) return 0;
  return (solution.match(/[\p{L}\p{N}]/gu) ?? []).length;
}

export function priorityColor(priority: string | null): string {
  switch ((priority || "").toLowerCase()) {
    case "high":
      return "red";
    case "low":
      return "gray";
    default:
      return "blue";
  }
}

export function statusColor(status: string | null): string {
  const s = (status || "").toLowerCase();
  if (s.includes("solved")) return "green";
  if (s.includes("stuck")) return "red";
  if (s.includes("progress") || s.includes("working")) return "yellow";
  if (s.includes("unstarted")) return "gray";
  return "blue";
}
