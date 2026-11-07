import { parseSheetUrl } from "./lib/sheetUrl.js";
import { createRegistry } from "./lib/registry.js";
import { cacheValid, stateFor } from "./lib/panelState.js";
import { bySheetPath, wsUrl } from "./lib/origin.js";
import { backoffDelay } from "./lib/backoff.js";
import { apiGet, getOrigin } from "./api.js";

const PANEL_PATH = "src/panel.html";
let registry = createRegistry();
const failures = new Map(); // tabId -> "auth" | "error" | "notfound" (transient)
const ports = new Set();
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

chrome.runtime.onInstalled.addListener(() => chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }));
chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});

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

async function refreshTab(tabId, url) {
  await ready;
  if (!parseSheetUrl(url ?? "")) {
    registry.delete(tabId);
    failures.delete(tabId);
    persist();
  } else {
    await lookup(tabId, url);
  }
  const snap = await snapshot(tabId);
  const parsedOk = !!parseSheetUrl(url ?? "");
  chrome.sidePanel.setOptions({ tabId, path: PANEL_PATH, enabled: parsedOk }).catch(() => {});
  broadcast(snap);
}

function broadcast(msg) {
  for (const p of ports) {
    try {
      p.postMessage(msg);
    } catch {
      ports.delete(p);
    }
  }
}

chrome.tabs.onUpdated.addListener((tabId, info, tab) => {
  if (info.url || info.status === "complete") refreshTab(tabId, tab.url);
});
chrome.tabs.onActivated.addListener(async ({ tabId }) => {
  const tab = await chrome.tabs.get(tabId).catch(() => null);
  if (tab) refreshTab(tabId, tab.url);
});
chrome.tabs.onRemoved.addListener(async (tabId) => {
  await ready;
  registry.delete(tabId);
  failures.delete(tabId);
  persist();
});

chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== "panel") return;
  ports.add(port);
  port.onDisconnect.addListener(() => ports.delete(port));
  port.postMessage({ type: "socket", connected: socketUp });
  port.onMessage.addListener(async (msg) => {
    if (msg.type !== "get") return;
    const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
    if (!tab) return;
    await refreshTab(tab.id, tab.url);
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

async function connect() {
  clearTimeout(timer);
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
