import { parseHuntBoardUrl } from "./origin.js";

const onOrigin = (url, origin) => {
  try {
    return new URL(url).origin === origin;
  } catch {
    return false;
  }
};

// Which open tab a new hunt-board tab should be folded into, or null to leave it alone.
// The caller passes only a new tab's first page (see createArrivals). A tab opened in the
// background from a dashboard tab (middle-click, Ctrl+click: a deliberate second view) and a
// tab alone in its window are never folded. A foreground tab is folded whatever its opener:
// Chrome names the active tab as the opener of a link opened from another app (Discord desktop).
export function boardReuseTarget({ newTab, tabs, origin }) {
  const uid = parseHuntBoardUrl(newTab.url, origin);
  if (!uid) return null;
  if (newTab.active !== true && newTab.openerTabId !== undefined) {
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
// link. Only then is the target focused and the new tab closed, so the link is never lost --
// and only if the new tab is still on the link: one navigated elsewhere (or closed) while
// the board was being asked is the user's now, so it stays and keeps the focus.
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
  const stillOnLink = await Promise.resolve()
    .then(() => api.currentUrl(newTabId))
    .then((now) => now === url, () => false);
  if (!stillOnLink) return result;
  await Promise.resolve().then(() => api.focus(targetId)).catch(() => {});
  await Promise.resolve().then(() => api.remove(newTabId)).catch(() => {});
  return result;
}

// Tabs created in the last `windowMs` that have not yet reached a real page. Only such a
// tab's first page is a new arrival a board link can be folded from: a reload, the SPA
// changing its own query, a same-tab navigation, and tabs open before the extension
// started are never arrivals, whatever the registry knows about them.
export function createArrivals(windowMs = 30000) {
  const fresh = new Map();
  const isReal = (url) => /^https?:/i.test(url ?? "");
  let quietEnd = 0;
  return {
    // Tabs created before `until` are not new (the browser restoring its session at startup).
    quietUntil(until) {
      quietEnd = until;
    },
    created(tabId, now) {
      if (now >= quietEnd) fresh.set(tabId, now);
    },
    arrived(tabId, url, now) {
      if (!fresh.has(tabId) || !isReal(url)) return false;
      const at = fresh.get(tabId);
      fresh.delete(tabId);
      return now - at <= windowMs;
    },
    removed(tabId) {
      fresh.delete(tabId);
    },
  };
}
