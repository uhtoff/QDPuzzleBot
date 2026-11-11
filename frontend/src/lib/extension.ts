// Bridge to the Puzzle Chat browser extension. Its content script marks the
// page with a data attribute (before the SPA mounts) and relays these window
// messages to the extension's background worker.

const SOURCE = "puzzbot-dashboard";

export function extensionPresent(): boolean {
  return typeof document !== "undefined" && document.documentElement?.dataset?.puzzbotExtension === "1";
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
