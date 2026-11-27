import { parseHuntBoardUrl } from "./origin.js";

const onOrigin = (url, origin) => {
  try {
    return new URL(url).origin === origin;
  } catch {
    return false;
  }
};

// Which open tab a new hunt-board tab should be folded into, or null to leave it alone.
// Only a new arrival at a hunt board counts (not a reload or the SPA changing its own
// query: `beforeUid` is the hunt the tab already showed). A tab opened from a dashboard
// tab (a deliberate second view) and a tab alone in its window are never folded.
export function boardReuseTarget({ newTab, beforeUid, tabs, origin }) {
  const uid = parseHuntBoardUrl(newTab.url, origin);
  if (!uid || beforeUid === uid) return null;
  if (newTab.openerTabId !== undefined) {
    const opener = tabs.find((t) => t.id === newTab.openerTabId);
    if (opener && onOrigin(opener.url, origin)) return null;
  }
  if (tabs.filter((t) => t.windowId === newTab.windowId).length <= 1) return null;
  const candidates = tabs.filter((t) => t.id !== newTab.id && parseHuntBoardUrl(t.url, origin) === uid);
  if (!candidates.length) return null;
  candidates.sort(
    (a, b) =>
      (b.windowId === newTab.windowId) - (a.windowId === newTab.windowId) ||
      (b.lastAccessed ?? 0) - (a.lastAccessed ?? 0),
  );
  return candidates[0].id;
}
