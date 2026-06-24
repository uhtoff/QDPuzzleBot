import { notifications } from "@mantine/notifications";

export function notifyError(e: unknown): void {
  const message = e instanceof Error ? e.message : String(e);
  notifications.show({ message, color: "red" });
}

export function notifySuccess(msg: string): void {
  notifications.show({ message: msg, color: "green" });
}

export function showSheetWarning(data: unknown): void {
  if (
    data &&
    typeof data === "object" &&
    "sheet_warning" in data &&
    (data as Record<string, unknown>).sheet_warning
  ) {
    notifications.show({
      message: String((data as Record<string, unknown>).sheet_warning),
      color: "yellow",
      autoClose: 8000,
    });
  }
}

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
