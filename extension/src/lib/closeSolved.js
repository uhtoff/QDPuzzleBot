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
