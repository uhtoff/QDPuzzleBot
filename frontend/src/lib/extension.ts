// Bridge to the Puzzle Chat browser extension. Its content script marks the
// page with a data attribute (before the SPA mounts) and relays these window
// messages to the extension's background worker.

const SOURCE = "puzzbot-dashboard";

export function extensionPresent(): boolean {
  return typeof document !== "undefined" && document.documentElement?.dataset?.puzzbotExtension === "1";
}

// What extension 1.0.0 relays. It published no action list, so a copy that
// marks the page without one is taken to support exactly these.
const LEGACY_ACTIONS = ["work", "openRound", "closeSolved"];

/** Can the installed extension act on this dashboard request? A copy that
 *  publishes its action list (data-puzzbot-actions) is taken at its word; one
 *  that publishes none is 1.0.0. Gate each extension button on its own action,
 *  so a dashboard deployed ahead of an extension update never shows a dead one. */
export function extensionSupports(action: string): boolean {
  if (!extensionPresent()) return false;
  const listed = document.documentElement.dataset.puzzbotActions;
  const actions = listed === undefined ? LEGACY_ACTIONS : listed.split(/\s+/);
  return actions.includes(action);
}

function send(action: string, payload: unknown): void {
  window.postMessage({ source: SOURCE, action, payload }, window.location.origin);
}

export function workOn(puzzle: { id: number; sheet_url: string | null; hunt_uid: string }): void {
  if (!puzzle.sheet_url) return;
  send("work", { puzzleId: puzzle.id, sheetUrl: puzzle.sheet_url, huntUid: puzzle.hunt_uid });
}

export function openPuzzle(name: string, sheetUrl: string, puzzleUrl: string): void {
  send("openPuzzle", { name, sheetUrl, puzzleUrl });
}

export function openRound(name: string, sheetUrls: string[]): void {
  send("openRound", { name, sheetUrls });
}

export function closeSolved(): void {
  send("closeSolved", {});
}

/** The "Puzzle page" link: a plain left click goes through the extension when it can
 *  open pages, so it remembers the tab (its chat shows beside it, Close solved closes it).
 *  Modified or middle clicks, no sheet, or an older extension: returns false and the
 *  link works as a normal link. */
export function openPageViaExtension(
  event: Pick<MouseEvent, "button" | "ctrlKey" | "metaKey" | "shiftKey" | "altKey" | "preventDefault">,
  sheetUrl: string | null,
  puzzleUrl: string,
): boolean {
  if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return false;
  if (!sheetUrl || !extensionSupports("openPage")) return false;
  event.preventDefault();
  send("openPage", { sheetUrl, puzzleUrl });
  return true;
}
