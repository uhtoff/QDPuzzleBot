// Hunt puzzle pages the extension opened itself (Open puzzle as a tab group),
// remembered by tab id with the puzzle's sheet. Without host access the
// extension cannot read a hunt page's URL, so this record is the only way
// Close solved can tell which page belongs to which puzzle.

const sheetKey = (spreadsheetId, gid) => `${spreadsheetId}#${gid ?? ""}`;

// `initial` is an entries() result (restored from session storage).
export function createPageTabs(initial = []) {
  const tabs = new Map(initial.map(([tabId, s]) => [tabId, { spreadsheetId: s.spreadsheetId, gid: s.gid ?? null }]));
  return {
    add(tabId, { spreadsheetId, gid }) {
      tabs.set(tabId, { spreadsheetId, gid: gid ?? null });
    },
    get(tabId) {
      const s = tabs.get(tabId);
      return s ? { ...s } : null;
    },
    remove: (tabId) => tabs.delete(tabId),
    replace(oldId, newId) {
      const s = tabs.get(oldId);
      if (!s) return false;
      tabs.delete(oldId);
      tabs.set(newId, s);
      return true;
    },
    entries: () => [...tabs.entries()].map(([id, s]) => [id, { ...s }]),
    // Each remembered sheet once, so Close solved asks the server once per puzzle.
    sheets() {
      const seen = new Map();
      for (const s of tabs.values()) seen.set(sheetKey(s.spreadsheetId, s.gid), { ...s });
      return [...seen.values()];
    },
  };
}

// The tab ids remembered for one sheet.
export function pageTabsFor(entries, spreadsheetId, gid) {
  const key = sheetKey(spreadsheetId, gid);
  return entries.filter(([, s]) => sheetKey(s.spreadsheetId, s.gid) === key).map(([id]) => id);
}
