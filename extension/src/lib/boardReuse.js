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

// Hand a board link to the open tab `targetId`: the dashboard content script takes the
// query without reloading when the page supports it (answers `true`); otherwise — a
// `false`, an error (no content script), or no answer in time — the tab navigates to the
// link. Only then is the target focused and the new tab closed, so the link is never lost.
export async function handOverBoardLink({ targetId, newTabId, url, api, timeoutMs = 1500 }) {
  const msg = { type: "openBoardLink", search: new URL(url).search };
  let answer;
  try {
    answer = await Promise.race([api.sendMessage(targetId, msg), new Promise((r) => setTimeout(() => r(undefined), timeoutMs))]);
  } catch {
    answer = undefined;
  }
  let result = "handed";
  if (answer !== true) {
    await api.update(targetId, { url });
    result = "reloaded";
  }
  await Promise.resolve().then(() => api.focus(targetId)).catch(() => {});
  await Promise.resolve().then(() => api.remove(newTabId)).catch(() => {});
  return result;
}
