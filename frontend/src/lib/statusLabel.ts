/**
 * Display formatting for stored status/priority values.
 *
 * Storage is canonically lowercase — bot/core/constants.py owns STATUSES /
 * PRIORITIES, and PuzzleService.update_puzzle_field lowercases on write — so
 * every user-facing surface title-cases at the edge instead. Sending the label
 * back as the value is what used to store "Very High" from this dashboard and
 * "very high" from the bot, in the same column.
 *
 * Rows written before migration 041 may still hold Title Case; title-casing is
 * idempotent, so those render unchanged.
 */
function titleCase(value: string): string {
  return value.replace(/\b\w/g, (c) => c.toUpperCase());
}

export function formatStatus(status: string | null): string {
  return status ? titleCase(status) : "";
}

export function formatPriority(priority: string | null): string {
  return priority ? titleCase(priority) : "";
}
