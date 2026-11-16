// tab -> puzzle memory. Stored puzzles carry `spreadsheetId` (the id the tab's
// URL was parsed from) alongside the server's by-sheet fields.
export function createRegistry(initial = []) {
  const tabs = new Map(initial);
  const where = (pred) => [...tabs].filter(([, p]) => pred(p)).map(([t]) => t);
  return {
    set: (tabId, puzzle) => void tabs.set(tabId, puzzle),
    get: (tabId) => tabs.get(tabId),
    entries: () => [...tabs].map(([t, p]) => [t, p]),
    delete: (tabId) => void tabs.delete(tabId),
    // Record which window the tab lives in; no-op for an unknown tab. Returns whether it changed.
    setWindow(tabId, windowId) {
      const p = tabs.get(tabId);
      if (!p || typeof windowId !== "number" || p.windowId === windowId) return false;
      tabs.set(tabId, { ...p, windowId });
      return true;
    },
    findBySpreadsheet(id) {
      const hit = where((p) => p.spreadsheetId === id);
      return hit.length ? hit[0] : null;
    },
    // A tab holding this spreadsheet for a files-mode puzzle (one file per
    // puzzle, so the spreadsheet identifies the puzzle). Tabs-mode puzzles share
    // one spreadsheet, so they are never matched.
    // A puzzle-page tab (`page: true`, a hunt page the extension opened) is never the sheet.
    findFilesTab(id, excludeTabId) {
      const hit = where((p) => p.spreadsheetId === id && p.sheet_mode === "files" && !p.page);
      return hit.find((t) => t !== excludeTabId) ?? null;
    },
    // The existing tab to switch to instead of `newTabId`, or null to leave it.
    reuseTarget(newTabId, spreadsheetId, windowTabCount) {
      if (windowTabCount <= 1) return null;
      const entry = tabs.get(newTabId);
      if (entry?.sheet_mode !== "files" || entry.page) return null;
      return this.findFilesTab(spreadsheetId, newTabId);
    },
    tabsForChannel: (channelId) => where((p) => p.channel_id === channelId),
    solvedTabs: () => where((p) => p.solved),
    update(puzzleId, patch) {
      for (const [t, p] of tabs) if (p.id === puzzleId) tabs.set(t, { ...p, ...patch });
    },
  };
}
