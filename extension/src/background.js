import { parseSheetUrl } from "./lib/sheetUrl.js";
import { createRegistry } from "./lib/registry.js";
import { cacheValid, stateFor, shouldDeliver } from "./lib/panelState.js";
import { bySheetPath, wsUrl } from "./lib/origin.js";
import { backoffDelay } from "./lib/backoff.js";
import { apiGet, getOrigin } from "./api.js";

const PANEL_PATH = "src/panel.html";
let registry = createRegistry();
const failures = new Map(); // tabId -> "auth" | "error" | "notfound" (transient)
const ports = new Map(); // port -> windowId
let socketUp = false;

// MV3: the worker is killed when idle; rehydrate the registry before any event.
const ready = (async () => {
  try {
    const { registryEntries } = await chrome.storage.session.get({ registryEntries: [] });
    registry = createRegistry(registryEntries);
  } catch {
    /* start empty */
  }
})();

const persist = () => chrome.storage.session.set({ registryEntries: registry.entries() }).catch(() => {});

// One global side panel for every tab; non-puzzle tabs show "not a puzzle".
function setupPanel() {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
  chrome.sidePanel.setOptions({ path: PANEL_PATH, enabled: true }).catch(() => {});
}
setupPanel();
chrome.runtime.onInstalled.addListener(async () => {
  setupPanel();
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
});

chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== "panel") return;
  ports.set(port, null);
  port.onDisconnect.addListener(() => ports.delete(port));
  safePost(port, { type: "socket", connected: socketUp });
  port.onMessage.addListener(async (msg) => {
    if (msg.type !== "get" || typeof msg.windowId !== "number") return;
    ports.set(port, msg.windowId);
    const [tab] = await chrome.tabs.query({ active: true, windowId: msg.windowId });
    if (tab) await refreshTab(tab, port);
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
    setSocket(true);
  };
  ws.onmessage = (e) => {
    let ev;
    try {
      ev = JSON.parse(e.data);
    } catch {
      return;
    }
    if (ev && ["message", "message_edit", "message_delete"].includes(ev.type)) broadcast({ type: "event", event: ev });
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
