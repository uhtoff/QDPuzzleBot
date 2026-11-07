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
    findBySpreadsheet(id) {
      const hit = where((p) => p.spreadsheetId === id);
      return hit.length ? hit[0] : null;
    },
    tabsForChannel: (channelId) => where((p) => p.channel_id === channelId),
    solvedTabs: () => where((p) => p.solved),
    update(puzzleId, patch) {
      for (const [t, p] of tabs) if (p.id === puzzleId) tabs.set(t, { ...p, ...patch });
    },
  };
}
