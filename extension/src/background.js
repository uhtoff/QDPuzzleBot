import { parseSheetUrl } from "./lib/sheetUrl.js";
import { createRegistry } from "./lib/registry.js";
import { cacheValid, stateFor, shouldDeliver } from "./lib/panelState.js";
import { bySheetPath, wsUrl } from "./lib/origin.js";
import { backoffDelay } from "./lib/backoff.js";
import { notificationId, parseNotificationId } from "./lib/notify.js";
import { createUnread, badgeText } from "./lib/unread.js";
import { apiGet, getOrigin } from "./api.js";

const PANEL_PATH = "src/panel.html";
let registry = createRegistry();
const failures = new Map(); // tabId -> "auth" | "error" | "notfound" (transient)
const ports = new Map(); // port -> windowId
let socketUp = false;
const isTracked = (channelId) => registry.tabsForChannel(channelId).length > 0;
let unread = createUnread({ isTracked });

// MV3: the worker is killed when idle; rehydrate the registry before any event.
const ready = (async () => {
  try {
    const { registryEntries } = await chrome.storage.session.get({ registryEntries: [] });
    registry = createRegistry(registryEntries);
    const { unreadEntries } = await chrome.storage.session.get({ unreadEntries: [] });
    unread = createUnread({ isTracked, initial: unreadEntries });
  } catch {
    /* start empty */
  }
  updateBadge();
})();

const persist = () =>
  chrome.storage.session.set({ registryEntries: registry.entries(), unreadEntries: unread.serialise() }).catch(() => {});

function updateBadge() {
  chrome.action.setBadgeText({ text: badgeText(unread.total()) }).catch(() => {});
}

// Channels of the puzzles shown in open panels (active tab of each panel's window).
async function visibleChannels() {
  const windows = new Set([...ports.values()].filter((w) => typeof w === "number"));
  const out = new Set();
  for (const windowId of windows) {
    const [tab] = await chrome.tabs.query({ active: true, windowId }).catch(() => []);
    const channelId = tab && registry.get(tab.id)?.channel_id;
    if (channelId) out.add(String(channelId));
  }
  return [...out];
}

// Clear counts for channels now visible or no longer backed by any tab.
async function reconcileUnread() {
  await ready;
  const visible = new Set(await visibleChannels());
  let cleared = false;
  for (const [channelId] of unread.serialise()) {
    if (visible.has(channelId) || !isTracked(channelId)) {
      unread.clear(channelId);
      cleared = true;
    }
  }
  if (!cleared) return;
  persist();
  updateBadge();
}

async function handleMessage(ev) {
  await ready;
  if (!unread.onEvent(ev, await visibleChannels())) return;
  persist();
  updateBadge();
  const { notify } = await chrome.storage.sync.get({ notify: false }).catch(() => ({ notify: false }));
  if (!notify) return;
  const channelId = String(ev.channel_id);
  const tabId = registry.tabsForChannel(channelId)[0];
  const text = `${ev.message?.author_name ?? ""}: ${ev.message?.content ?? ""}`;
  const tab = await chrome.tabs.get(tabId).catch(() => null);
  if (!tab) return;
  chrome.notifications
    .create(notificationId(tabId, tab.windowId), {
      type: "basic",
      iconUrl: chrome.runtime.getURL("icons/icon128.png"),
      title: registry.get(tabId)?.name ?? "Puzzle chat",
      message: text.length > 120 ? text.slice(0, 119) + "\u2026" : text,
    })
    .catch(() => {});
}

chrome.notifications.onClicked.addListener(async (id) => {
  const target = parseNotificationId(id);
  if (!target) return;
  chrome.notifications.clear(id);
  const tab = await chrome.tabs.get(target.tabId).catch(() => null);
  if (!tab) return;
  chrome.windows.update(tab.windowId, { focused: true }).catch(() => {});
  chrome.tabs.update(tab.id, { active: true }).catch(() => {});
});

// One global side panel for every tab; non-puzzle tabs show "not a puzzle".
function setupPanel() {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
  chrome.sidePanel.setOptions({ path: PANEL_PATH, enabled: true }).catch(() => {});
}
setupPanel();
chrome.runtime.onInstalled.addListener(async (details) => {
  setupPanel();
  if (details.reason !== "install") return;
  const origin = await getOrigin();
  const ok = await chrome.permissions.contains({ origins: [origin + "/*"] }).catch(() => false);
  if (!ok) chrome.runtime.openOptionsPage();
});

async function lookup(tabId, url) {
  const parsed = parseSheetUrl(url);
  if (!parsed) {
    registry.delete(tabId);
    failures.delete(tabId);
    persist();
    return;
  }
  if (cacheValid(registry.get(tabId), parsed)) return;
  const res = await apiGet(bySheetPath(parsed));
  if (res.authRequired) {
    registry.delete(tabId);
    failures.set(tabId, "auth");
  } else if (res.error) {
    registry.delete(tabId);
    failures.set(tabId, res.status === 404 ? "notfound" : "error");
  } else {
    failures.delete(tabId);
    registry.set(tabId, { ...res.puzzle, bridge_enabled: res.bridge_enabled, spreadsheetId: parsed.spreadsheetId, gid: parsed.gid });
  }
  persist();
}

async function snapshot(tabId) {
  const puzzle = registry.get(tabId) ?? null;
  return { type: "puzzle", tabId, puzzle, state: stateFor(puzzle ?? undefined, failures.get(tabId)) };
}

// Refresh a tab's state; deliver the snapshot to panels of that tab's window
// when it is the active tab. `only` restricts delivery to one port.
async function refreshTab(tab, only) {
  await ready;
  const tabId = tab.id;
  if (!parseSheetUrl(tab.url ?? "")) {
    registry.delete(tabId);
    failures.delete(tabId);
    persist();
  } else {
    await lookup(tabId, tab.url);
  }
  const snap = await snapshot(tabId);
  for (const [p, windowId] of ports) {
    if ((only && p !== only) || !shouldDeliver(windowId, tab)) continue;
    safePost(p, snap);
  }
  reconcileUnread();
}

function safePost(p, msg) {
  try {
    p.postMessage(msg);
  } catch {
    ports.delete(p);
  }
}

function broadcast(msg) {
  for (const p of ports.keys()) safePost(p, msg);
}

chrome.tabs.onUpdated.addListener((tabId, info, tab) => {
  if (info.url || info.status === "complete") refreshTab(tab);
});
chrome.tabs.onActivated.addListener(async ({ tabId }) => {
  const tab = await chrome.tabs.get(tabId).catch(() => null);
  if (tab) refreshTab(tab);
});
chrome.tabs.onRemoved.addListener(async (tabId) => {
  await ready;
  registry.delete(tabId);
  failures.delete(tabId);
  persist();
  reconcileUnread();
});

chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== "panel") return;
  ports.set(port, null);
  port.onDisconnect.addListener(() => {
    ports.delete(port);
    reconcileUnread();
  });
  safePost(port, { type: "socket", connected: socketUp });
  port.onMessage.addListener(async (msg) => {
    if (msg.type !== "get" || typeof msg.windowId !== "number") return;
    ports.set(port, msg.windowId);
    const [tab] = await chrome.tabs.query({ active: true, windowId: msg.windowId });
    if (tab) await refreshTab(tab, port);
    else reconcileUnread();
  });
});

// ---- websocket ----
let ws = null;
let attempt = 0;
let timer = null;

function setSocket(up) {
  if (socketUp !== up) {
    socketUp = up;
    broadcast({ type: "socket", connected: up });
  }
}

// Keep the worker alive while the socket is open/connecting or a reconnect is pending.
let keepalive = null;
function ensureKeepalive() {
  if (keepalive) return;
  keepalive = setInterval(() => {
    const live = (ws && ws.readyState <= 1) || timer !== null;
    if (!live) {
      clearInterval(keepalive);
      keepalive = null;
      return;
    }
    chrome.runtime.getPlatformInfo(() => {});
  }, 20000);
}

async function connect() {
  clearTimeout(timer);
  timer = null;
  ensureKeepalive();
  const origin = await getOrigin();
  try {
    ws = new WebSocket(wsUrl(origin));
  } catch {
    scheduleReconnect();
    return;
  }
  ws.onopen = () => {
    attempt = 0;
    ensureKeepalive();
    setSocket(true);
  };
  ws.onmessage = (e) => {
    let ev;
    try {
      ev = JSON.parse(e.data);
    } catch {
      return;
    }
    if (ev && ["message", "message_edit", "message_delete"].includes(ev.type)) {
      broadcast({ type: "event", event: ev });
      if (ev.type === "message") handleMessage(ev);
    }
  };
  ws.onclose = () => {
    setSocket(false);
    scheduleReconnect();
  };
  ws.onerror = () => {}; // onclose follows
}

function scheduleReconnect() {
  clearTimeout(timer);
  timer = setTimeout(connect, backoffDelay(attempt++));
}

chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "sync" && changes.dashboardOrigin) {
    attempt = 0;
    if (ws) ws.onclose = null;
    ws?.close();
    setSocket(false);
    connect();
  }
});

connect();
