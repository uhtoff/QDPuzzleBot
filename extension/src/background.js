import { parseSheetUrl } from "./lib/sheetUrl.js";
import { createRegistry } from "./lib/registry.js";
import { cacheValid, stateFor, shouldDeliver } from "./lib/panelState.js";
import { bySheetPath, wsUrl, WS_TICKET_PATH } from "./lib/origin.js";
import { backoffDelay, reconnectVia, resolveAttempt } from "./lib/backoff.js";
import { createLookupGen } from "./lib/lookupGen.js";
import { shouldApplyLookup, shouldCloseTab, planWindowClose } from "./lib/closeSolved.js";
import { notificationId, parseNotificationId, shouldNotify } from "./lib/notify.js";
import { createNameCache, resolveName } from "./lib/channelNames.js";
import { notificationText } from "./lib/discordText.js";
import { createUnread, badgeText } from "./lib/unread.js";
import { validateAction, isNewArrival } from "./lib/actions.js";
import { groupMove } from "./lib/groupMove.js";
import { pickColour, findRoundGroup } from "./lib/groupColour.js";
import { needsSocket } from "./lib/connection.js";
import { apiGet, getOrigin } from "./api.js";

const PANEL_PATH = "src/panel.html";
let registry = createRegistry();
const lookupGen = createLookupGen();
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
  // Not awaited: the scan does network lookups and must not stall every handler waiting on `ready`.
  scanOpenTabs();
})();

// storage.session is empty after install/reload/browser restart and restored tabs fire no
// events until visited, so register already-open sheet tabs here. No tab reuse: not new arrivals.
async function scanOpenTabs() {
  const tabs = await chrome.tabs.query({ url: "https://docs.google.com/spreadsheets/*" }).catch(() => []);
  for (const tab of tabs) {
    if (registry.get(tab.id)) continue;
    try {
      await refreshTab(tab);
    } catch {
      /* one bad tab must not stop the scan */
    }
  }
}

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

const names = createNameCache({
  fetcher: async (huntUid) => {
    const res = await apiGet("/api/hunts/" + encodeURIComponent(huntUid) + "/puzzles");
    if (!Array.isArray(res?.puzzles)) throw new Error("no puzzles");
    return Object.fromEntries(res.puzzles.filter((p) => p.channel_id).map((p) => [String(p.channel_id), p.name]));
  },
});
const registryName = (id) => registry.entries().find(([, p]) => p.channel_id === id)?.[1]?.name;
// Bounded wait so a slow dashboard never stalls a notification or snapshot.
const withTimeout = (promise, ms, fallback) => Promise.race([promise, new Promise((r) => setTimeout(() => r(fallback), ms))]);
const huntNames = (huntUid, wantId) => withTimeout(names.ensure(huntUid, wantId), 3000, {});

async function handleMessage(ev) {
  await ready;
  if (!unread.onEvent(ev, await visibleChannels())) return;
  persist();
  updateBadge();
  const { notify } = await chrome.storage.sync.get({ notify: false }).catch(() => ({ notify: false }));
  if (!notify || !shouldNotify(ev.message)) return;
  const channelId = String(ev.channel_id);
  const tabId = registry.tabsForChannel(channelId)[0];
  const huntMap = await huntNames(registry.get(tabId)?.hunt_uid, wantedChannel(ev.message));
  const text = notificationText(ev.message, (id) => resolveName(id, huntMap, registryName));
  const tab = await chrome.tabs.get(tabId).catch(() => null);
  if (!tab) return;
  chrome.notifications
    .create(notificationId(tabId, tab.windowId), {
      type: "basic",
      iconUrl: chrome.runtime.getURL("icons/icon128.png"),
      title: registry.get(tabId)?.name ?? "Puzzle chat",
      message: text,
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

// Content script that lets the dashboard drive the extension. Registered only
// for the configured origin, and only once its host permission is granted.
const DASHBOARD_SCRIPT_ID = "puzzbot-dashboard";
async function registerDashboardScript() {
  const origin = await getOrigin();
  await chrome.scripting.unregisterContentScripts({ ids: [DASHBOARD_SCRIPT_ID] }).catch(() => {}); // may not be registered
  const ok = await chrome.permissions.contains({ origins: [origin + "/*"] }).catch(() => false);
  if (!ok) return;
  await chrome.scripting
    .registerContentScripts([
      { id: DASHBOARD_SCRIPT_ID, matches: [origin + "/*"], js: ["src/dashboard.js"], runAt: "document_start" },
    ])
    .catch(() => {});
}
registerDashboardScript();
chrome.permissions.onAdded.addListener(() => registerDashboardScript());
chrome.permissions.onRemoved.addListener(() => registerDashboardScript());

chrome.runtime.onInstalled.addListener(async (details) => {
  setupPanel();
  registerDashboardScript();
  if (details.reason !== "install") return;
  const origin = await getOrigin();
  const ok = await chrome.permissions.contains({ origins: [origin + "/*"] }).catch(() => false);
  if (!ok) chrome.runtime.openOptionsPage();
});

// Returns false when a newer lookup for the same tab (or the tab closing) made this one stale.
async function lookup(tabId, url) {
  const token = lookupGen.begin(tabId);
  const parsed = parseSheetUrl(url);
  if (!parsed) {
    registry.delete(tabId);
    failures.delete(tabId);
    persist();
    return true;
  }
  if (cacheValid(registry.get(tabId), parsed)) return true;
  const res = await apiGet(bySheetPath(parsed));
  if (!lookupGen.isCurrent(tabId, token)) return false;
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
  return true;
}

// First channel mention in a message (content or embeds), to trigger a refetch on a miss.
function wantedChannel(message) {
  const text = [message?.content, ...(message?.embeds ?? []).flatMap((e) => [e?.title, e?.description])].join(" ");
  return /<#(\d+)>/.exec(text)?.[1];
}

const namesFor = (huntUid, huntMap) => ({
  ...Object.fromEntries(registry.entries().filter(([, p]) => p.channel_id).map(([, p]) => [p.channel_id, p.name])),
  ...huntMap,
});

// Fetch (single-flight) off the snapshot path; tell panels when new names land.
function warmNames(huntUid) {
  const before = names.cached(huntUid);
  names.ensure(huntUid).then((map) => {
    if (map === before) return;
    const msg = { type: "names", huntUid, channelNames: namesFor(huntUid, map) };
    for (const [p] of ports) safePost(p, msg);
  });
}

async function snapshot(tabId) {
  const puzzle = registry.get(tabId) ?? null;
  const channelNames = namesFor(puzzle?.hunt_uid, names.cached(puzzle?.hunt_uid));
  if (puzzle?.hunt_uid) warmNames(puzzle.hunt_uid);
  return { type: "puzzle", tabId, puzzle, channelNames, state: stateFor(puzzle ?? undefined, failures.get(tabId)) };
}

// Refresh a tab's state; deliver the snapshot to panels of that tab's window
// when it is the active tab. `only` restricts delivery to one port.
async function refreshTab(tab, only) {
  await ready;
  const tabId = tab.id;
  if (!parseSheetUrl(tab.url ?? "")) {
    lookupGen.begin(tabId); // invalidate any in-flight lookup for the old URL
    registry.delete(tabId);
    failures.delete(tabId);
    persist();
  } else if (!(await lookup(tabId, tab.url))) {
    return; // superseded: the newer refresh delivers
  }
  const token = lookupGen.current(tabId);
  const snap = await snapshot(tabId);
  // The captured `tab.active` may be stale after the awaits: ask the browser now.
  const fresh = await chrome.tabs.get(tabId).catch(() => null);
  if (!lookupGen.isCurrent(tabId, token)) return; // a newer lookup began meanwhile
  for (const [p, windowId] of ports) {
    if ((only && p !== only) || !shouldDeliver(windowId, fresh)) continue;
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

// A files-mode puzzle's sheet already open in another tab: switch there and
// drop the duplicate, unless the duplicate is its window's only tab.
async function maybeReuse(tabId) {
  const entry = registry.get(tabId);
  if (!entry || entry.sheet_mode !== "files") return;
  const tab = await chrome.tabs.get(tabId).catch(() => null);
  if (!tab) return;
  const inWindow = await chrome.tabs.query({ windowId: tab.windowId }).catch(() => []);
  const target = registry.reuseTarget(tabId, entry.spreadsheetId, inWindow.length);
  if (target === null) return;
  const existing = await chrome.tabs.get(target).catch(() => null);
  if (!existing) {
    registry.delete(target);
    persist();
    return;
  }
  const current = (await moveIntoGroup(tabId, target)) ?? existing;
  await focusTab(current);
  // Re-check after the awaits: the target may have been closed meanwhile.
  if (!registry.get(target) || !(await chrome.tabs.get(target).catch(() => null))) return;
  chrome.tabs.remove(tabId).catch(() => {});
}

// The duplicate may sit in a tab group (e.g. a restored saved group): the
// existing tab takes its place there, so the group keeps the puzzle.
async function moveIntoGroup(newId, existingId) {
  try {
    const [newTab, existing] = await Promise.all([chrome.tabs.get(newId), chrome.tabs.get(existingId)]);
    const m = groupMove(newTab, existing);
    if (!m) return existing;
    if (m.crossWindow) await chrome.tabs.move(existing.id, { windowId: m.windowId, index: m.index });
    await chrome.tabs.group({ groupId: m.groupId, tabIds: [existing.id] });
    await chrome.tabs.move(existing.id, { index: m.index });
  } catch {
    // Pinned, or the tab/group vanished: dedupe as before.
  }
  // Current state (the move may have changed its window).
  return chrome.tabs.get(existingId).catch(() => null);
}

// Serialised so two duplicates resolving together cannot close each other.
let reuseChain = Promise.resolve();
const queueReuse = (tabId) => {
  reuseChain = reuseChain.then(() => maybeReuse(tabId)).catch(() => {});
};

async function focusTab(tab) {
  await chrome.tabs.update(tab.id, { active: true }).catch(() => {});
  await chrome.windows.update(tab.windowId, { focused: true }).catch(() => {});
}

chrome.tabs.onUpdated.addListener((tabId, info, tab) => {
  if (!(info.url || info.status === "complete")) return;
  (async () => {
    await ready;
    const before = registry.get(tabId)?.spreadsheetId;
    await refreshTab(tab);
    // Only a tab newly arriving at a spreadsheet is a duplicate candidate, not a reload or in-file navigation.
    if (isNewArrival(before, registry.get(tabId)?.spreadsheetId)) queueReuse(tabId);
  })();
});
chrome.tabs.onActivated.addListener(async ({ tabId }) => {
  const tab = await chrome.tabs.get(tabId).catch(() => null);
  if (tab) refreshTab(tab);
});
chrome.tabs.onRemoved.addListener(async (tabId) => {
  lookupGen.drop(tabId); // synchronous: before any in-flight lookup can resolve
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

// ---- dashboard actions (from src/dashboard.js) ----

async function workOn(url, senderTab) {
  const parsed = parseSheetUrl(url);
  const tabId = registry.findFilesTab(parsed.spreadsheetId);
  const existing = tabId === null ? null : await chrome.tabs.get(tabId).catch(() => null);
  if (existing) {
    // Bring it into the dashboard's window so the side panel opened there shows it.
    if (existing.windowId !== senderTab.windowId) {
      await chrome.tabs.move(existing.id, { windowId: senderTab.windowId, index: -1 }).catch(() => {});
    }
    return focusTab((await chrome.tabs.get(existing.id).catch(() => null)) ?? existing);
  }
  await chrome.tabs.create({ url, windowId: senderTab.windowId, active: true });
}

// Tab already showing this exact sheet link (same file, and same gid when given).
function sameSheet(a, b) {
  return a.spreadsheetId === b.spreadsheetId && (a.gid ?? null) === (b.gid ?? null);
}

// Serialised so quick clicks cannot pick the same colour or lose a groupColours write.
let roundChain = Promise.resolve();
const queueOpenRound = (name, urls, senderTab) => {
  roundChain = roundChain.then(() => openRound(name, urls, senderTab)).catch(() => {});
  return roundChain;
};

async function openRound(name, urls, senderTab) {
  const windowId = senderTab.windowId;
  const open = await chrome.tabs.query({ url: "https://docs.google.com/spreadsheets/*" }).catch(() => []);
  const tabIds = [];
  for (const url of urls) {
    const want = parseSheetUrl(url);
    // Files mode: the spreadsheet identifies the puzzle (Sheets rewrites #gid, so don't compare it).
    const regId = registry.findFilesTab(want.spreadsheetId);
    const regTab = regId !== null && !tabIds.includes(regId) ? await chrome.tabs.get(regId).catch(() => null) : null;
    const have =
      regTab ??
      open.find((t) => {
        const p = parseSheetUrl(t.url ?? "");
        return p && sameSheet(p, want) && !tabIds.includes(t.id);
      });
    if (have) {
      if (have.windowId !== windowId) await chrome.tabs.move(have.id, { windowId, index: -1 }).catch(() => {});
      tabIds.push(have.id);
    } else {
      const created = await chrome.tabs.create({ url, windowId, active: false }).catch(() => null);
      if (created) tabIds.push(created.id);
    }
  }
  if (!tabIds.length) return;
  const key = name.trim().toLowerCase();
  const readStored = async () => (await chrome.storage.local.get("groupColours").catch(() => ({}))).groupColours ?? {};
  const remember = async (colour) => {
    const latest = await readStored();
    await chrome.storage.local.set({ groupColours: { ...latest, [key]: colour } }).catch(() => {});
  };
  const stored = await readStored();
  const groups = await chrome.tabGroups.query({ windowId }).catch(() => []);
  const existingGroup = findRoundGroup(name, groups);
  // On any failure (pinned tab, vanished group) the tabs stay open, ungrouped.
  try {
    if (existingGroup) {
      // Reuse the open group; leave its colour alone (record it if not yet remembered).
      await chrome.tabs.group({ groupId: existingGroup.id, tabIds });
      if (!stored[key]) await remember(existingGroup.color);
      return;
    }
    const colour = pickColour(name, groups.map((g) => g.color), stored[key]);
    const groupId = await chrome.tabs.group({ tabIds, createProperties: { windowId } });
    await chrome.tabGroups.update(groupId, { title: name, color: colour });
    await remember(colour);
  } catch {
    // leave the tabs ungrouped
  }
}

// Re-ask the server about every registered tab (cached solved flags go stale)
// and close those whose puzzle is now solved. A window is never left empty.
async function closeSolved() {
  const entries = registry.entries().filter(([, e]) => e.sheet_mode === "files"); // files mode only
  const results = await Promise.allSettled(
    entries.map(async ([tabId, entry]) => ({
      tabId,
      entry,
      res: await apiGet(bySheetPath({ spreadsheetId: entry.spreadsheetId, gid: entry.gid ?? null })),
    })),
  );
  const solved = []; // {tabId, spreadsheetId}
  for (const r of results) {
    if (r.status !== "fulfilled") continue;
    const { tabId, entry, res } = r.value;
    if (res.authRequired || res.error || !res.puzzle) continue;
    // The tab may have closed or moved on while we waited: never re-insert it.
    const current = registry.get(tabId);
    if (!shouldApplyLookup(current, entry.spreadsheetId)) continue;
    registry.set(tabId, { ...current, ...res.puzzle, bridge_enabled: res.bridge_enabled });
    if (res.puzzle.solved) solved.push({ tabId, spreadsheetId: entry.spreadsheetId });
  }
  persist();
  const stillSolved = async (list) =>
    (
      await Promise.all(
        list.map(async (s) => {
          const tab = await chrome.tabs.get(s.tabId).catch(() => null);
          return shouldCloseTab(tab, s.spreadsheetId) ? tab : null;
        }),
      )
    ).filter(Boolean);
  const tabs = await stillSolved(solved);
  for (const windowId of new Set(tabs.map((t) => t.windowId))) {
    // Re-check first (navigated away / dragged elsewhere), then size the window against what really closes.
    const candidates = await stillSolved(solved.filter((s) => tabs.some((t) => t.id === s.tabId && t.windowId === windowId)));
    const all = await chrome.tabs.query({ windowId }).catch(() => []);
    const { closing, needBlank } = planWindowClose(all, candidates, windowId);
    if (!closing.length) continue;
    if (needBlank) await chrome.tabs.create({ windowId, active: false }).catch(() => {});
    await chrome.tabs.remove(closing).catch(() => {});
  }
}

async function handleDashboardAction(msg, sender) {
  await ready;
  const v = validateAction(msg, {
    senderId: sender.id,
    senderUrl: sender.url,
    senderHasTab: !!sender.tab,
    runtimeId: chrome.runtime.id,
    origin: await getOrigin(),
  });
  if (!v.ok) return;
  if (v.action === "work") await workOn(v.payload.sheetUrl, sender.tab);
  else if (v.action === "openRound") {
    if (v.payload.sheetUrls.length) await queueOpenRound(v.payload.name, v.payload.sheetUrls, sender.tab);
  } else if (v.action === "closeSolved") await closeSolved();
}

chrome.runtime.onMessage.addListener((msg, sender) => {
  // "Work on this puzzle" also tries to open the side panel. This must run
  // synchronously, before any await, to stay inside the click's user gesture;
  // Chrome may still reject it, which is fine.
  if (msg && msg.action === "work" && sender.id === chrome.runtime.id && sender.tab) {
    chrome.sidePanel.open({ windowId: sender.tab.windowId }).catch(() => {});
  }
  handleDashboardAction(msg, sender).catch(() => {});
});

// ---- websocket ----
let ws = null;
let attempt = 0;
// A woken worker must resume the backoff, not restart it at 1 s: persist the counter.
let attemptTouched = false;
const attemptLoaded = chrome.storage.session
  .get({ reconnectAttempt: 0 })
  .then((r) => {
    if (!attemptTouched) attempt = resolveAttempt(r.reconnectAttempt);
  })
  .catch(() => {});
function setAttempt(n) {
  attemptTouched = true;
  attempt = n;
  chrome.storage.session.set({ reconnectAttempt: n }).catch(() => {});
}
let timer = null;

function setSocket(up) {
  if (socketUp !== up) {
    socketUp = up;
    broadcast({ type: "socket", connected: up });
  }
}

// Single in-flight guard: connect() awaits before assigning `ws`, so concurrent callers
// (startup, permissions.onAdded, origin change, reconnect timer) must share one attempt.
let connecting = null;
let restartConnect = false;
function connect() {
  if (connecting) return connecting;
  connecting = doConnect()
    .catch(() => {})
    .finally(() => {
      connecting = null;
      if (restartConnect) {
        restartConnect = false;
        connect();
      }
    });
  return connecting;
}

// Drop the current socket without triggering its reconnect handler.
function detachSocket() {
  if (!ws) return;
  ws.onopen = ws.onmessage = ws.onclose = ws.onerror = null;
  ws.close();
  ws = null;
}

async function doConnect() {
  clearTimeout(timer);
  timer = null;
  chrome.alarms.clear(RECONNECT_ALARM).catch(() => {});
  const origin = await getOrigin();
  // No host permission yet (fresh install): stay idle; permissions.onAdded reconnects.
  const granted = await chrome.permissions.contains({ origins: [origin + "/*"] }).catch(() => false);
  if (!granted) return;
  if (!needsSocket(ws?.readyState)) return; // another path already connected
  // Chrome withholds the session cookie from a service-worker websocket, so
  // authenticate with a fresh single-use ticket fetched over cookie-carrying HTTP.
  const t = await apiGet(WS_TICKET_PATH);
  if (!t || t.authRequired || t.error || !t.ticket) {
    scheduleReconnect(); // logged out / dashboard unreachable: idle with backoff
    return;
  }
  if (!needsSocket(ws?.readyState)) return; // connected while we awaited the ticket
  detachSocket();
  try {
    ws = new WebSocket(wsUrl(origin, t.ticket));
  } catch {
    scheduleReconnect();
    return;
  }
  ws.onopen = () => {
    setAttempt(0);
    setSocket(true);
  };
  ws.onmessage = (e) => {
    let ev;
    try {
      ev = JSON.parse(e.data);
    } catch {
      return;
    }
    if (ev && ev.type === "ping") return; // server heartbeat: its arrival is what keeps the worker alive
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

// Long waits go through chrome.alarms so the worker may sleep while logged out/unreachable.
const RECONNECT_ALARM = "puzzbot-reconnect";
async function scheduleReconnect() {
  await attemptLoaded;
  clearTimeout(timer);
  timer = null;
  chrome.alarms.clear(RECONNECT_ALARM).catch(() => {});
  const delay = backoffDelay(attempt);
  setAttempt(attempt + 1);
  if (reconnectVia(delay) === "alarm") chrome.alarms.create(RECONNECT_ALARM, { when: Date.now() + delay });
  else timer = setTimeout(connect, delay);
}
// Registered at top level so an alarm that wakes a fresh worker still reconnects.
// A periodic watchdog covers a dead link (e.g. after laptop sleep) with nothing else to wake the worker.
const WATCHDOG_ALARM = "puzzbot-watchdog";
chrome.alarms.get(WATCHDOG_ALARM).then((a) => {
  if (!a) chrome.alarms.create(WATCHDOG_ALARM, { periodInMinutes: 1 });
}).catch(() => {});
chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name === RECONNECT_ALARM) connect();
  else if (alarm.name === WATCHDOG_ALARM && needsSocket(ws?.readyState)) {
    // A backoff retry already pending (timer or alarm) owns the schedule.
    if (timer !== null || (await chrome.alarms.get(RECONNECT_ALARM).catch(() => null))) return;
    connect();
  }
});

chrome.permissions.onAdded.addListener(() => {
  setAttempt(0);
  if (needsSocket(ws?.readyState)) connect();
});

chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "sync" && changes.dashboardOrigin) {
    registerDashboardScript();
    setAttempt(0);
    detachSocket();
    setSocket(false);
    if (connecting) restartConnect = true; // in-flight attempt may hold the old origin
    else connect();
  }
});

connect();
