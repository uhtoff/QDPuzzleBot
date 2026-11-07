import { parseSheetUrl } from "./sheetUrl.js";

// May a lookup result for `spreadsheetId` be written back? Only if the tab is
// still registered for that same spreadsheet (a closed tab must not be resurrected).
export function shouldApplyLookup(current, spreadsheetId) {
  return !!current && current.spreadsheetId === spreadsheetId;
}

// Is it still safe to close `tab` as the solved puzzle `spreadsheetId`?
export function shouldCloseTab(tab, spreadsheetId) {
  return !!tab && parseSheetUrl(tab.url ?? "")?.spreadsheetId === spreadsheetId;
}

// Which of the freshly re-checked `candidates` to close in `windowId`, and whether
// a blank tab is needed first (only if every tab in that window is being closed).
// A candidate dragged to another window meanwhile is not this window's to close.
export function planWindowClose(windowTabs, candidates, windowId) {
  const closing = candidates.filter((t) => t.windowId === windowId).map((t) => t.id);
  const needBlank = closing.length > 0 && windowTabs.every((t) => closing.includes(t.id));
  return { closing, needBlank };
}
