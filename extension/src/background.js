import { parseSheetUrl } from "./lib/sheetUrl.js";
import { createRegistry } from "./lib/registry.js";
import { cacheValid, huntCacheValid, stateFor, shouldDeliver } from "./lib/panelState.js";
import { bySheetPath, huntChatPath, huntUnreadPath, parseHuntBoardUrl, wsUrl, WS_TICKET_PATH, removesOrigin, SHEETS_ORIGINS } from "./lib/origin.js";
import { backoffDelay, reconnectVia, resolveAttempt } from "./lib/backoff.js";
import { invalidateOnNavigate } from "./lib/tabRefresh.js";
import { createLookupGen } from "./lib/lookupGen.js";
import { shouldApplyLookup, canClose, planWindowClose } from "./lib/closeSolved.js";
import { createPageTabs, pageTabsFor } from "./lib/pageTabs.js";
import { notificationId, parseNotificationId, shouldNotify } from "./lib/notify.js";
import { createNameCache, resolveName } from "./lib/channelNames.js";
import { notificationText } from "./lib/discordText.js";
import { createMarkers, snowflakeAt } from "./lib/readMarkers.js";
import { applyMessage, badgeCount, badgeText, clearAll, clearChannel, isListed, listRows, peekPuzzle, totals } from "./lib/unreadList.js";
import { validateAction, isNewArrival } from "./lib/actions.js";
import { panelWindow } from "./lib/panelWindow.js";
import { groupMove } from "./lib/groupMove.js";
import { pickColour, findRoundGroup, findPuzzleGroup } from "./lib/groupColour.js";
import { needsSocket } from "./lib/connection.js";
import { createPanelHost } from "./lib/panelHost.js";
import { apiGet, apiSend, getOrigin } from "./api.js";

const PANEL_PATH = "src/panel.html";
// Chrome side panel or Firefox sidebar (Firefox cannot open it from the handlers below).
const panelHost = createPanelHost({
  sidePanel: chrome.sidePanel,
  sidebarAction: globalThis.browser?.sidebarAction ?? chrome.sidebarAction,
  action: chrome.action,
  path: PANEL_PATH,
});
let registry = createRegistry();
let pageTabs = createPageTabs(); // hunt pages opened by Open puzzle: tabId -> its sheet
const lookupGen = createLookupGen();
const failures = new Map(); // tabId -> "auth" | "error" | "notfound" (transient)
const failedFor = new Map(); // tabId -> {spreadsheetId, gid} the failure was recorded for
const ports = new Map(); // port -> windowId
let socketUp = false;

// ---- unread list state ----
// Read markers persist in chrome.storage.local per dashboard origin. The server answers
// "what is newer than these" per hunt (POST /api/hunts/<uid>/unread); huntCache holds
// that answer, kept current from live events between fetches.
const MARKERS_KEY = "readMarkers";
let markers = createMarkers();
let markersOrigin = null;
let markersVersion = 0; // bumped by mark all / undo: a fetch begun before it is stale
let markersSaveTimer = null;
const huntCache = new Map(); // huntUid -> channels[]
const portHunt = new Map(); // port -> the (unarchived) hunt its panel last showed
const peeks = new Map(); // port -> {huntUid, channelId}: a chat shown instead of the tab's
const misses = new Set(); // channel ids no cached hunt knows, until the next board event
const huntFetches = new Map(); // huntUid -> {again, promise}
const huntFailed = new Map(); // huntUid -> ms of its last failed fetch (e.g. an older dashboard)
const HUNT_RETRY_MS = 60000;
const UNDO_MS = 8000;
let undo = null; // {prev, caches, count, timer} after Mark all read

// MV3: the worker is killed when idle; rehydrate the registry before any event.
const ready = (async () => {
  try {
    const { registryEntries } = await chrome.storage.session.get({ registryEntries: [] });
    registry = createRegistry(registryEntries);
    const { pageTabEntries } = await chrome.storage.session.get({ pageTabEntries: [] });
    pageTabs = createPageTabs(pageTabEntries);
  } catch {
    /* start empty */
  }
  await loadMarkers();
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
  chrome.storage.session
    .set({ registryEntries: registry.entries(), pageTabEntries: pageTabs.entries() })
    .catch(() => {});

// The toolbar badge: unread in chats that have a tab open.
function updateBadge() {
  const all = [...huntCache.values()].flat();
  const tracked = registry.entries().map(([, p]) => p.channel_id).filter(Boolean);
  chrome.action.setBadgeText({ text: badgeText(badgeCount(all, tracked)) }).catch(() => {});
}

// What a panel is showing: a peeked chat, else its window's active tab. `tabEntry` is
// the tab's puzzle either way. A non-puzzle tab keeps the hunt the panel last showed.
async function portView(port) {
  const windowId = ports.get(port);
  const [tab] = typeof windowId === "number" ? await chrome.tabs.query({ active: true, windowId }).catch(() => []) : [];
  const tabEntry = tab ? registry.get(tab.id) : undefined;
  const peek = peeks.get(port);
  if (peek) return { huntUid: peek.huntUid, channelId: peek.channelId, tabEntry };
  const huntUid = tabEntry ? (tabEntry.hunt_archived ? null : (tabEntry.hunt_uid ?? null)) : (portHunt.get(port) ?? null);
  return { huntUid, channelId: tabEntry?.channel_id ?? null, tabEntry };
}

// Channels shown in open panels: being read, so not counted.
async function visibleChannels() {
  const out = new Set();
  for (const port of ports.keys()) {
    if (typeof ports.get(port) !== "number") continue;
    const { channelId } = await portView(port);
    if (channelId) out.add(String(channelId));
  }
  return [...out];
}

// The panels' views changed (tab switch, panel closed, tab closed): re-send lists.
async function reconcileUnread() {
  await ready;
  await pushUnread();
}

async function loadMarkers() {
  const origin = await getOrigin();
  const got = await chrome.storage.local.get({ [MARKERS_KEY]: {} }).catch(() => ({}));
  markers = createMarkers(got?.[MARKERS_KEY]?.[origin]);
  markersOrigin = origin;
  // First run: everything already said counts as read.
  if (markers.ensureBaseline(Date.now())) await saveMarkersNow();
}

async function saveMarkersNow() {
  clearTimeout(markersSaveTimer);
  markersSaveTimer = null;
  const origin = markersOrigin;
  if (!origin) return;
  const got = await chrome.storage.local.get({ [MARKERS_KEY]: {} }).catch(() => null);
  if (!got) return;
  const all = { ...(got[MARKERS_KEY] ?? {}), [origin]: markers.request() };
  await chrome.storage.local.set({ [MARKERS_KEY]: all }).catch(() => {});
}

// Looking at a chat moves its marker on every push: batch the writes.
function saveMarkers() {
  if (!markersSaveTimer) markersSaveTimer = setTimeout(saveMarkersNow, 2000);
}

// Hunts worth keeping a list for: those panels show, and those of open puzzle tabs (badge).
function wantedHunts() {
  const out = new Set(portHunt.values());
  for (const { huntUid } of peeks.values()) out.add(huntUid);
  for (const [, p] of registry.entries()) if (p.hunt_uid && !p.hunt_archived) out.add(p.hunt_uid);
  return out;
}

async function doFetchUnread(uid) {
  const version = markersVersion;
  const origin = markersOrigin;
  const known = huntCache.get(uid);
  const res = await apiSend(huntUnreadPath(uid), "POST", markers.request(known?.map((c) => c.channel_id)));
  if (origin !== markersOrigin) return "done"; // the dashboard changed meanwhile
  if (version !== markersVersion) return "stale"; // marked all read meanwhile: ask again
  if (!res || res.authRequired || res.error || !Array.isArray(res.channels)) {
    huntCache.delete(uid); // the list hides; nothing else changes
    huntFailed.set(uid, Date.now());
    return "done";
  }
  huntFailed.delete(uid);
  const before = new Map((huntCache.get(uid) ?? []).map((c) => [c.channel_id, c]));
  const now = snowflakeAt(Date.now());
  // The server sends no solve time: a solve sorts from when it was first seen.
  huntCache.set(
    uid,
    res.channels.map((c) => (c.newly_solved ? { ...c, solved_seq: before.get(c.channel_id)?.solved_seq ?? now } : c)),
  );
  for (const c of res.channels) misses.delete(c.channel_id);
  return "done";
}

// Single-flight per hunt; a request made while one is in flight runs once more after it.
function fetchUnread(uid) {
  const inflight = huntFetches.get(uid);
  if (inflight) {
    inflight.again = true;
    return inflight.promise;
  }
  const entry = { again: false, promise: null };
  entry.promise = (async () => {
    let outcome;
    do {
      entry.again = false;
      outcome = await doFetchUnread(uid).catch(() => "done");
    } while (entry.again || outcome === "stale");
    huntFetches.delete(uid);
    await pushUnread();
  })();
  huntFetches.set(uid, entry);
  return entry.promise;
}

const refetchWanted = () => {
  for (const uid of wantedHunts()) fetchUnread(uid);
};

// The tab's own puzzle, for the peek bar's "Back to"; null when not peeking.
function peekInfo(port, view, chans) {
  if (!peeks.has(port)) return null;
  const e = view.tabEntry;
  const own = e?.channel_id ? chans?.find((c) => c.channel_id === e.channel_id) : null;
  return { name: e?.name ?? null, channelId: e?.channel_id ?? null, unread: own?.unread ?? 0 };
}

// Send each panel its list; a chat on screen is read up to now.
async function pushUnread() {
  await ready;
  let moved = false;
  const now = Date.now();
  for (const port of [...ports.keys()]) {
    if (typeof ports.get(port) !== "number") continue;
    const view = await portView(port);
    const uid = view.huntUid;
    let chans = uid ? huntCache.get(uid) : undefined;
    const seen = view.channelId ? chans?.find((c) => c.channel_id === view.channelId) : null;
    if (seen) {
      if (markers.look(seen.channel_id, seen.last?.id, now)) moved = true;
      if (seen.unread || seen.newly_solved) {
        chans = clearChannel(chans, seen.channel_id);
        huntCache.set(uid, chans);
      }
    }
    const rows = chans ? listRows(chans, view.channelId) : [];
    safePost(port, {
      type: "unread",
      huntUid: chans ? uid : null,
      rows,
      totals: totals(rows),
      undo: undo?.count ?? 0,
      peek: peekInfo(port, view, chans),
    });
  }
  if (moved) saveMarkers();
  updateBadge();
  for (const uid of wantedHunts()) {
    if (huntCache.has(uid) || huntFetches.has(uid)) continue;
    if (Date.now() - (huntFailed.get(uid) ?? 0) < HUNT_RETRY_MS) continue;
    fetchUnread(uid);
  }
}

function notePortHunt(port, puzzle) {
  if (puzzle?.hunt_uid && !puzzle.hunt_archived) portHunt.set(port, puzzle.hunt_uid);
}

const findChannel = (huntUid, channelId) => huntCache.get(huntUid)?.find((c) => c.channel_id === String(channelId)) ?? null;

// Show another chat in this panel; the browser tab stays put. null = back to the tab.
async function peekChat(port, huntUid, channelId) {
  await ready;
  const windowId = ports.get(port);
  const ch = channelId ? findChannel(huntUid, channelId) : null;
  const view = await portView(port);
  if (!ch || view.tabEntry?.channel_id === ch.channel_id) {
    peeks.delete(port);
    const [tab] = await chrome.tabs.query({ active: true, windowId }).catch(() => []);
    if (tab) await refreshTab(tab, port);
    else await pushUnread();
    return;
  }
  peeks.set(port, { huntUid, channelId: ch.channel_id });
  warmNames(huntUid);
  const bridgeOn = view.tabEntry?.bridge_enabled !== false;
  safePost(port, {
    type: "puzzle",
    tabId: null,
    puzzle: { ...peekPuzzle(ch, huntUid), bridge_enabled: bridgeOn },
    channelNames: namesFor(huntUid, names.cached(huntUid)),
    state: bridgeOn ? "chat" : "unavailable",
    peek: peekInfo(port, view, huntCache.get(huntUid)),
  });
  await pushUnread();
}

// Go to the chat's puzzle: its open tab (this window first), else its sheet in a new
// tab, else (general chat, or no sheet) the hunt board.
async function gotoChat(port, huntUid, channelId) {
  await ready;
  const windowId = ports.get(port);
  const ch = findChannel(huntUid, channelId);
  if (!ch || typeof windowId !== "number") return;
  peeks.delete(port);
  const tabs = (await Promise.all(registry.tabsForChannel(ch.channel_id).map((id) => chrome.tabs.get(id).catch(() => null)))).filter(Boolean);
  const tab = tabs.find((t) => t.windowId === windowId) ?? tabs[0];
  if (tab) {
    await focusTab(tab);
    // Already the active tab (no activation event follows): show it now.
    const fresh = await chrome.tabs.get(tab.id).catch(() => null);
    if (fresh && fresh.windowId === windowId) await refreshTab(fresh, port);
    return;
  }
  const url = ch.kind === "puzzle" && ch.sheet_url ? ch.sheet_url : (await getOrigin()) + "/hunts/" + encodeURIComponent(huntUid);
  await chrome.tabs.create({ url, windowId, active: true }).catch(() => {});
}

function markAllRead() {
  const prev = markers.markAll(Date.now());
  markersVersion++;
  const caches = new Map(huntCache);
  let count = 0;
  for (const [uid, chans] of huntCache) {
    count += chans.filter(isListed).length;
    huntCache.set(uid, clearAll(chans));
  }
  clearTimeout(undo?.timer);
  undo = {
    prev,
    caches,
    count,
    timer: setTimeout(() => {
      undo = null;
      pushUnread();
    }, UNDO_MS),
  };
  saveMarkersNow();
  pushUnread();
}

function undoMarkAll() {
  if (!undo) return;
  clearTimeout(undo.timer);
  markers.restore(undo.prev);
  markersVersion++;
  for (const [uid, chans] of undo.caches) huntCache.set(uid, chans);
  undo = null;
  saveMarkersNow();
  pushUnread();
  for (const uid of huntCache.keys()) fetchUnread(uid); // catch up on anything said meanwhile
}

// A board change (a solve, a new puzzle) may change a hunt's list: refetch, debounced.
const boardTimers = new Map();
function boardChanged(uid) {
  if (!wantedHunts().has(uid) && !huntCache.has(uid)) return;
  clearTimeout(boardTimers.get(uid));
  boardTimers.set(
    uid,
    setTimeout(() => {
      boardTimers.delete(uid);
      misses.clear();
      fetchUnread(uid);
    }, 1500),
  );
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
  const channelId = ev.channel_id == null ? null : String(ev.channel_id);
  if (!channelId) return;
  const visible = await visibleChannels();
  let known = false;
  for (const [uid, chans] of huntCache) {
    const r = applyMessage(chans, ev, { visible, markers, selfId: null });
    if (!r.known) continue;
    known = true;
    huntCache.set(uid, r.channels);
  }
  // A chat no cached hunt knows (e.g. a puzzle created a moment ago): ask once.
  if (!known && ev.message?.kind === 0 && !misses.has(channelId)) {
    misses.add(channelId);
    refetchWanted();
  }
  await pushUnread();
  // Desktop notifications, as before: a chat with an open tab that no panel shows.
  if (!registry.tabsForChannel(channelId).length || visible.includes(channelId)) return;
  const { notify } = await chrome.storage.sync.get({ notify: false }).catch(() => ({ notify: false }));
  if (!notify || !shouldNotify(ev.message)) return;
  // "notifications" is an optional permission: skip silently until/unless granted.
  if (!chrome.notifications || !(await chrome.permissions.contains({ permissions: ["notifications"] }).catch(() => false))) return;
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

// chrome.notifications is undefined until the optional permission is granted, so register
// on startup if present and again when the permission is added.
let notificationClickRegistered = false;
function registerNotificationClick() {
  if (notificationClickRegistered || !chrome.notifications) return;
  notificationClickRegistered = true;
  chrome.notifications.onClicked.addListener(async (id) => {
    const target = parseNotificationId(id);
    if (!target) return;
    // Synchronously, before any await, to stay inside the click's user gesture; Chrome may still reject it.
    panelHost.open(target.windowId);
    chrome.notifications.clear(id);
    const tab = await chrome.tabs.get(target.tabId).catch(() => null);
    if (!tab) return;
    chrome.windows.update(tab.windowId, { focused: true }).catch(() => {});
    chrome.tabs.update(tab.id, { active: true }).catch(() => {});
  });
}
registerNotificationClick();
chrome.permissions.onAdded.addListener(registerNotificationClick);

// One global panel (Chrome side panel / Firefox sidebar) for every tab; non-puzzle
// tabs show "not a puzzle".
function setupPanel() {
  panelHost.setup();
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
chrome.permissions.onRemoved.addListener(async (removed) => {
  registerDashboardScript();
  // Access to the current dashboard was revoked: forget everything learned from it.
  if (removesOrigin(removed.origins, await getOrigin())) {
    await resetDashboardState();
  }
});

chrome.runtime.onInstalled.addListener(async (details) => {
  setupPanel();
  registerDashboardScript();
  if (details.reason !== "install") return;
  const origin = await getOrigin();
  const ok = await chrome.permissions.contains({ origins: [origin + "/*"] }).catch(() => false);
  // Firefox can install without the Sheets permission granted (or it can be revoked later).
  const sheetsOk = await chrome.permissions.contains({ origins: SHEETS_ORIGINS }).catch(() => false);
  if (!ok || !sheetsOk) chrome.runtime.openOptionsPage();
});

// Returns false when a newer lookup for the same tab (or the tab closing) made this one stale.
// `parsed` is the sheet to look up: the tab's own, or (`page`) the remembered sheet of a
// hunt page the extension opened, registered with `page: true` so it is never taken for the sheet.
async function lookup(tabId, parsed, windowId, page = false) {
  const token = lookupGen.begin(tabId);
  if (!parsed) {
    registry.delete(tabId);
    failures.delete(tabId);
    failedFor.delete(tabId);
    persist();
    return true;
  }
  if (cacheValid(registry.get(tabId), parsed, page)) {
    if (registry.setWindow(tabId, windowId)) persist();
    return true;
  }
  const res = await apiGet(bySheetPath(parsed));
  if (!lookupGen.isCurrent(tabId, token)) return false;
  if (res.authRequired) {
    registry.delete(tabId);
    failures.set(tabId, "auth");
    failedFor.set(tabId, parsed);
  } else if (res.error) {
    registry.delete(tabId);
    failures.set(tabId, res.status === 404 ? "notfound" : "error");
    failedFor.set(tabId, parsed);
  } else {
    failures.delete(tabId);
    failedFor.delete(tabId);
    const entry = { ...res.puzzle, bridge_enabled: res.bridge_enabled, spreadsheetId: parsed.spreadsheetId, gid: parsed.gid, windowId };
    registry.set(tabId, page ? { ...entry, page: true } : entry);
  }
  persist();
  return true;
}

// A hunt board on the dashboard shows that hunt's general channel. Same contract as
// lookup(). The entry has no sheet, id or sheet_mode, so the sheet-only paths
// (close solved, files-mode tab reuse) never match it.
async function lookupHunt(tabId, uid, windowId) {
  const token = lookupGen.begin(tabId);
  if (huntCacheValid(registry.get(tabId), uid)) {
    if (registry.setWindow(tabId, windowId)) persist();
    return true;
  }
  const res = await apiGet(huntChatPath(uid));
  if (!lookupGen.isCurrent(tabId, token)) return false;
  registry.delete(tabId);
  failedFor.delete(tabId);
  if (res.authRequired) failures.set(tabId, "auth");
  else if (res.error) failures.set(tabId, res.status === 404 ? "notfound" : "error");
  else {
    failures.delete(tabId);
    registry.set(tabId, { ...res.puzzle, bridge_enabled: res.bridge_enabled, windowId });
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
  return { type: "puzzle", tabId, puzzle, channelNames, state: stateFor(puzzle ?? undefined, failures.get(tabId)), peek: null };
}

// Refresh a tab's state; deliver the snapshot to panels of that tab's window
// when it is the active tab. `only` restricts delivery to one port.
async function refreshTab(tab, only) {
  await ready;
  const tabId = tab.id;
  if (invalidateOnNavigate({ tab, registry, failures, failedFor, lookupGen, ports, post: safePost })) persist();
  const sheet = parseSheetUrl(tab.url ?? "");
  // A hunt page the extension opened shows its puzzle's chat. Its URL is unreadable while it
  // is on the hunt site. A dashboard hunt board shows the hunt's general channel; any
  // other readable non-sheet URL is "not a puzzle".
  const pageSheet = !sheet && !tab.url && !tab.pendingUrl ? pageTabs.get(tabId) : null;
  const huntUid = !sheet && !pageSheet ? parseHuntBoardUrl(tab.url ?? "", await getOrigin()) : null;
  if (huntUid) {
    if (!(await lookupHunt(tabId, huntUid, tab.windowId))) return; // superseded
  } else if (!sheet && !pageSheet) {
    lookupGen.begin(tabId); // invalidate any in-flight lookup for the old URL
    registry.delete(tabId);
    failures.delete(tabId);
    failedFor.delete(tabId);
    persist();
  } else if (!(await lookup(tabId, sheet ?? pageSheet, tab.windowId, !sheet))) {
    return; // superseded: the newer refresh delivers
  }
  const token = lookupGen.current(tabId);
  const snap = await snapshot(tabId);
  // The captured `tab.active` may be stale after the awaits: ask the browser now.
  const fresh = await chrome.tabs.get(tabId).catch(() => null);
  if (!lookupGen.isCurrent(tabId, token)) return; // a newer lookup began meanwhile
  for (const [p, windowId] of ports) {
    if ((only && p !== only) || !shouldDeliver(windowId, fresh)) continue;
    if (peeks.has(p)) continue; // showing another chat: Back or a tab switch returns to this one
    notePortHunt(p, snap.puzzle);
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
chrome.tabs.onAttached.addListener(async (tabId, { newWindowId }) => {
  await ready;
  if (registry.setWindow(tabId, newWindowId)) persist();
});
chrome.tabs.onCreated.addListener(async (tab) => {
  await ready;
  if (registry.setWindow(tab.id, tab.windowId)) persist();
});
chrome.tabs.onActivated.addListener(async ({ tabId, windowId }) => {
  // Switching tab ends a peek: the panel follows the tab again. Before any await.
  for (const [p, w] of ports) if (w === windowId) peeks.delete(p);
  await ready;
  if (registry.setWindow(tabId, windowId)) persist();
  const tab = await chrome.tabs.get(tabId).catch(() => null);
  if (tab) refreshTab(tab);
});
chrome.tabs.onRemoved.addListener(async (tabId) => {
  lookupGen.drop(tabId); // synchronous: before any in-flight lookup can resolve
  await ready;
  registry.delete(tabId);
  pageTabs.remove(tabId);
  failures.delete(tabId);
  failedFor.delete(tabId);
  persist();
  reconcileUnread();
});
// A prerendered/discarded tab swapped for a new id keeps its page record.
chrome.tabs.onReplaced.addListener(async (addedTabId, removedTabId) => {
  await ready;
  if (pageTabs.replace(removedTabId, addedTabId)) persist();
});

chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== "panel") return;
  ports.set(port, null);
  port.onDisconnect.addListener(() => {
    ports.delete(port);
    portHunt.delete(port);
    peeks.delete(port);
    reconcileUnread();
  });
  safePost(port, { type: "socket", connected: socketUp });
  port.onMessage.addListener(async (msg) => {
    if (!msg || typeof msg.windowId !== "number") return;
    ports.set(port, msg.windowId);
    if (msg.type === "get") {
      peeks.delete(port); // "show me my tab"
      const [tab] = await chrome.tabs.query({ active: true, windowId: msg.windowId });
      if (tab) await refreshTab(tab, port);
      else reconcileUnread();
    } else if (msg.type === "peek") await peekChat(port, msg.huntUid, msg.channelId ?? null);
    else if (msg.type === "goto") await gotoChat(port, msg.huntUid, msg.channelId);
    else if (msg.type === "markAllRead") markAllRead();
    else if (msg.type === "undoMarkAll") undoMarkAll();
  });
});

// ---- dashboard actions (from src/dashboard.js) ----

async function workOn(url, senderTab) {
  const parsed = parseSheetUrl(url);
  const tabId = registry.findFilesTab(parsed.spreadsheetId);
  const existing = tabId === null ? null : await chrome.tabs.get(tabId).catch(() => null);
  if (existing) return focusTab(existing); // stays where it is; its own window is focused
  await chrome.tabs.create({ url, windowId: senderTab.windowId, active: true });
}

// Tab already showing this exact sheet link (same file, and same gid when given).
function sameSheet(a, b) {
  return a.spreadsheetId === b.spreadsheetId && (a.gid ?? null) === (b.gid ?? null);
}

// Serialised so quick clicks cannot pick the same colour or lose a groupColours write.
let groupChain = Promise.resolve();
const queueOpen = (open) => {
  groupChain = groupChain.then(open).catch(() => {});
  return groupChain;
};

// Open (or reuse) each URL as a background tab in the sender's window.
// `onPageTab(tabId)` is told about each non-sheet (puzzle page) tab created.
async function openTabs(urls, windowId, onPageTab) {
  const open = await chrome.tabs.query({ url: "https://docs.google.com/spreadsheets/*" }).catch(() => []);
  const tabIds = [];
  for (const url of urls) {
    const want = parseSheetUrl(url);
    if (!want) {
      // A non-sheet page (the puzzle page). Without host permission its open
      // tabs' URLs are unreadable, so always create it rather than try to reuse.
      const created = await chrome.tabs.create({ url, windowId, active: false }).catch(() => null);
      if (created) {
        tabIds.push(created.id);
        onPageTab?.(created.id);
      }
      continue;
    }
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
  return tabIds;
}

// `reuse`: add to an open group with this title (rounds). Puzzles pass false: their group
// was already looked for by identity (findPuzzleGroup), so a same-named group is someone else's.
async function groupTabs(name, tabIds, windowId, reuse = true) {
  if (!tabIds.length) return;
  const key = name.trim().toLowerCase();
  const readStored = async () => (await chrome.storage.local.get("groupColours").catch(() => ({}))).groupColours ?? {};
  const remember = async (colour) => {
    const latest = await readStored();
    await chrome.storage.local.set({ groupColours: { ...latest, [key]: colour } }).catch(() => {});
  };
  const stored = await readStored();
  const groups = await chrome.tabGroups.query({ windowId }).catch(() => []);
  const existingGroup = reuse ? findRoundGroup(name, groups) : null;
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

async function openRound(name, urls, senderTab) {
  const tabIds = await openTabs(urls, senderTab.windowId);
  await groupTabs(name, tabIds, senderTab.windowId);
}

// An individual puzzle: its sheet plus the puzzle page from the hunt, in a
// group named after the puzzle. A second click focuses that puzzle's own group
// (one holding its remembered page; a round's group holds its meta's sheet too)
// instead of creating duplicate tabs.
async function openPuzzle(name, urls, senderTab) {
  const windowId = senderTab.windowId;
  const want = parseSheetUrl(urls[0]); // validated sheet URL
  const groups = await chrome.tabGroups.query({ windowId }).catch(() => []);
  // Only same-named groups can be this puzzle's; learn which puzzles their tabs hold.
  const tabsOf = new Map();
  for (const g of groups) {
    if (!findRoundGroup(name, [g])) continue;
    const tabs = await chrome.tabs.query({ groupId: g.id }).catch(() => []);
    tabsOf.set(
      g.id,
      tabs.map((t) => {
        const pageSheet = pageTabs.get(t.id);
        return pageSheet ? { ...pageSheet, page: true } : { ...registry.get(t.id), page: false };
      }),
    );
  }
  const existing = findPuzzleGroup(name, groups, tabsOf, want);
  if (existing) {
    const tabs = await chrome.tabs.query({ groupId: existing.id }).catch(() => []);
    if (tabs[0]) await focusTab(tabs[0]);
    return;
  }
  // Remember the page against its sheet so its chat shows and Close solved can close it.
  const tabIds = await openTabs(urls, windowId, (id) => {
    if (want) pageTabs.add(id, want);
    persist();
  });
  await groupTabs(name, tabIds, windowId, false);
}

// The dashboard's "Puzzle page" link, routed through the extension: open the page as a
// normal click would (active, beside the dashboard tab) and remember it against its sheet,
// so its chat shows in the panel and Close solved can close it.
async function openPage(sheetUrl, puzzleUrl, senderTab) {
  const sheet = parseSheetUrl(sheetUrl);
  const created = await chrome.tabs
    .create({ url: puzzleUrl, windowId: senderTab.windowId, index: senderTab.index + 1, openerTabId: senderTab.id, active: true })
    .catch(() => null);
  if (!created || !sheet) return;
  pageTabs.add(created.id, sheet);
  persist();
  refreshTab(created).catch(() => {});
}

// Re-ask the server about every registered tab (cached solved flags go stale)
// and close those whose puzzle is now solved, plus any hunt page Open puzzle
// opened for a solved puzzle (looked up even if its sheet tab is gone).
// A window is never left empty.
async function closeSolved() {
  // Files mode only; page tabs close through the page pass below.
  const entries = registry.entries().filter(([, e]) => e.sheet_mode === "files" && !e.page);
  const sheetLookups = entries.map(async ([tabId, entry]) => ({
    kind: "sheet",
    tabId,
    entry,
    res: await apiGet(bySheetPath({ spreadsheetId: entry.spreadsheetId, gid: entry.gid ?? null })),
  }));
  const pageLookups = pageTabs.sheets().map(async (sheet) => ({
    kind: "page",
    sheet,
    res: await apiGet(bySheetPath(sheet)),
  }));
  const results = await Promise.allSettled([...sheetLookups, ...pageLookups]);
  const solved = []; // {kind, tabId, spreadsheetId}
  for (const r of results) {
    if (r.status !== "fulfilled") continue;
    const { kind, res } = r.value;
    if (res.authRequired || res.error || !res.puzzle) continue;
    if (kind === "page") {
      // Page lookups never touch the registry: the page is not a sheet tab.
      if (!res.puzzle.solved) continue;
      const { spreadsheetId, gid } = r.value.sheet;
      for (const tabId of pageTabsFor(pageTabs.entries(), spreadsheetId, gid)) {
        solved.push({ kind, tabId, spreadsheetId });
      }
      continue;
    }
    const { tabId, entry } = r.value;
    // The tab may have closed or moved on while we waited: never re-insert it.
    const current = registry.get(tabId);
    if (!shouldApplyLookup(current, entry.spreadsheetId)) continue;
    registry.set(tabId, { ...current, ...res.puzzle, bridge_enabled: res.bridge_enabled });
    if (res.puzzle.solved) solved.push({ kind, tabId, spreadsheetId: entry.spreadsheetId });
  }
  persist();
  const itemFor = (tabId) => solved.find((s) => s.tabId === tabId);
  const stillSolved = async (list) =>
    (
      await Promise.all(
        list.map(async (s) => {
          const tab = await chrome.tabs.get(s.tabId).catch(() => null);
          return canClose(s, tab) ? tab : null;
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
    // Re-check each tab now, before deciding on a blank tab, so none is left when nothing closes.
    const ready2 = (
      await Promise.all(
        closing.map(async (id) => (canClose(itemFor(id), await chrome.tabs.get(id).catch(() => null)) ? id : null)),
      )
    ).filter((id) => id !== null);
    if (ready2.length !== closing.length) continue; // something changed: leave this window for the next run
    if (needBlank) await chrome.tabs.create({ windowId, active: false }).catch(() => {});
    for (const tabId of closing) {
      // Final check immediately before removal: no other await between get and remove.
      const tab = await chrome.tabs.get(tabId).catch(() => null);
      if (canClose(itemFor(tabId), tab)) chrome.tabs.remove(tabId).catch(() => {});
    }
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
  else if (v.action === "openPage") await openPage(v.payload.sheetUrl, v.payload.puzzleUrl, sender.tab);
  else if (v.action === "openPuzzle") {
    await queueOpen(() => openPuzzle(v.payload.name, [v.payload.sheetUrl, v.payload.puzzleUrl], sender.tab));
  } else if (v.action === "openRound") {
    if (v.payload.sheetUrls.length) await queueOpen(() => openRound(v.payload.name, v.payload.sheetUrls, sender.tab));
  } else if (v.action === "closeSolved") await closeSolved();
}

chrome.runtime.onMessage.addListener((msg, sender) => {
  // "Work on this puzzle" also tries to open the side panel. This must run
  // synchronously, before any await, to stay inside the click's user gesture;
  // Chrome may still reject it, which is fine.
  if (msg && msg.action === "work" && sender.id === chrome.runtime.id && sender.tab) {
    // Open where the puzzle's tab already lives (registry is synchronous; falls back to the sender's window).
    const parsed = parseSheetUrl(msg.payload?.sheetUrl ?? "");
    const tabId = parsed ? registry.findFilesTab(parsed.spreadsheetId) : null;
    const entry = tabId === null ? null : registry.get(tabId);
    panelHost.open(panelWindow(entry, sender.tab.windowId));
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
    // (Re)connected: anything said while the link was down is missing from the lists.
    if (up) ready.then(refetchWanted);
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
  if ((await getOrigin()) !== origin) return; // origin changed while we awaited: the restart handles it
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
    } else if (ev && ev.type === "board" && ev.uid) boardChanged(ev.uid);
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
    // No live events while the link is down: keep open panels' lists roughly current.
    if (ports.size) ready.then(refetchWanted);
    // A backoff retry already pending (timer or alarm) owns the schedule.
    if (timer !== null || (await chrome.alarms.get(RECONNECT_ALARM).catch(() => null))) return;
    connect();
  }
});

chrome.permissions.onAdded.addListener(() => {
  setAttempt(0);
  if (needsSocket(ws?.readyState)) connect();
});

// The dashboard origin changed: everything learned from the old one is wrong for the new one.
// Drop everything learned from the old origin and tell open panels to drop their
// history and composer.
async function resetDashboardState() {
  await ready; // else the pending session hydration overwrites the reset state with the old origin's
  detachSocket();
  setSocket(false);
  lookupGen.reset(); // in-flight lookups against the old origin must not write back
  names.clear();
  failures.clear();
  failedFor.clear();
  registry = createRegistry();
  pageTabs = createPageTabs(); // the old server's puzzles
  await saveMarkersNow(); // the old origin's read markers, before switching
  markersVersion++;
  huntCache.clear();
  huntFailed.clear();
  portHunt.clear();
  peeks.clear();
  misses.clear();
  clearTimeout(undo?.timer);
  undo = null;
  await loadMarkers();
  persist();
  updateBadge();
  broadcast({ type: "reset" });
}

async function switchOrigin() {
  registerDashboardScript();
  setAttempt(0);
  await resetDashboardState();
  if (connecting) restartConnect = true; // in-flight attempt abandons itself (origin re-check) then restarts
  else connect();
  scanOpenTabs();
}

chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "sync" && changes.dashboardOrigin) {
    switchOrigin();
  }
});

connect();
