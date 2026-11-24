import { rewriteMentions, tokenise, embedLines, emojiUrl } from "./lib/discordText.js";
import { isBlank, messageView, isBangCommand, errorText } from "./lib/format.js";
import { canCompose, validate, draftAfterSend } from "./lib/composer.js";
import { applyEvent, eventChannel, sameTarget, mergeHistory, targetChannel } from "./lib/panelState.js";
import { attachmentView } from "./lib/attachments.js";
import { messagesPath, safeAvatarUrl } from "./lib/origin.js";
import { paginate, rowText } from "./lib/unreadList.js";
import { apiGet, apiSend, getOrigin } from "./api.js";

const KIND_COMMAND = 3;
const POLL_MS = 15000;
const head = document.getElementById("head");
const notice = document.getElementById("notice");
const log = document.getElementById("log");
const composer = document.getElementById("composer");
const draft = document.getElementById("draft");
const sendBtn = document.getElementById("send");
const composerError = document.getElementById("composer-error");
const peekBar = document.getElementById("peek");
const unreadBox = document.getElementById("unread");

let channelId = null;
let messages = [];
let socketUp = false;
let pollTimer = null;
let loadSeq = 0;
let setStateSeq = 0; // newer setState invalidates this one's awaited notices
let pendingEvents = []; // live events seen while a history load is in flight

function showNotice(text, href) {
  notice.replaceChildren();
  if (!text) {
    notice.hidden = true;
    return;
  }
  if (href) {
    const a = document.createElement("a");
    a.href = href;
    a.target = "_blank";
    a.rel = "noopener";
    a.textContent = text;
    notice.append(a);
  } else {
    notice.textContent = text;
  }
  notice.hidden = false;
}

let currentPuzzle = null;
let channelNames = {};
let dashOrigin = null; // refreshed in loadMessages; used for attachment links

// Mentions, shortcodes, bold, code and custom emoji as DOM nodes; never innerHTML.
function appendInline(parent, raw) {
  const cleaned = rewriteMentions(raw, (id) => channelNames[id] || (id === currentPuzzle?.channel_id ? currentPuzzle.name : null), { keepCustomEmoji: true });
  for (const seg of tokenise(cleaned)) {
    if (seg.emoji) {
      const img = document.createElement("img");
      img.className = "emoji";
      img.src = emojiUrl(seg.emoji);
      img.alt = seg.text;
      img.title = seg.text;
      parent.append(img);
    } else if (seg.bold || seg.code) {
      const span = document.createElement(seg.code ? "code" : "strong");
      span.textContent = seg.text;
      parent.append(span);
    } else parent.append(seg.text);
  }
}

function renderMessage(m) {
  const v = messageView(m);
  const row = document.createElement("div");
  row.className = "msg" + (m.kind === KIND_COMMAND || isBangCommand(v.text) ? " dim" : "");
  const url = safeAvatarUrl(v.avatar);
  if (url) {
    const img = document.createElement("img");
    img.className = "avatar";
    img.src = url;
    img.alt = "";
    row.append(img);
  } else {
    const ph = document.createElement("div");
    ph.className = "avatar";
    row.append(ph);
  }
  const body = document.createElement("div");
  body.className = "body";
  const meta = document.createElement("div");
  meta.className = "meta";
  const who = document.createElement("strong");
  who.textContent = v.author ?? "";
  const when = document.createElement("time");
  const d = v.time ? new Date(v.time) : null;
  when.textContent = d && !isNaN(d) ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "";
  meta.append(who, " ", when);
  const text = document.createElement("div");
  text.className = "text";
  appendInline(text, v.text);
  body.append(meta, text);
  for (const e of Array.isArray(m.embeds) ? m.embeds : []) {
    const lines = embedLines(e);
    if (!lines.length) continue;
    const box = document.createElement("div");
    box.className = "embed";
    for (const l of lines) {
      const d = document.createElement("div");
      d.className = "embed-" + l.kind;
      appendInline(d, l.text);
      box.append(d);
    }
    body.append(box);
  }
  if (isBlank(m)) {
    text.textContent = "(no text)";
    text.classList.add("attach");
  }
  if (Array.isArray(m.attachments)) {
    for (const att of m.attachments) {
      const view = attachmentView(att, dashOrigin);
      const line = document.createElement("div");
      line.className = "attach";
      if (view.href) {
        const a = document.createElement("a");
        a.href = view.href;
        a.target = "_blank";
        a.rel = "noopener";
        a.textContent = view.text;
        line.append(a);
      } else line.textContent = view.text;
      body.append(line);
    }
  }
  row.append(body);
  return row;
}

function renderLog() {
  const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
  log.replaceChildren(...messages.map(renderMessage));
  if (atBottom) log.scrollTop = log.scrollHeight;
}

async function loadMessages() {
  if (!channelId) return;
  const seq = ++loadSeq;
  pendingEvents = [];
  dashOrigin = await getOrigin();
  const res = await apiGet(messagesPath(channelId));
  if (seq !== loadSeq) return;
  if (res.authRequired) return setState({ state: "auth" });
  if (res.error) return showNotice("Could not load messages (" + res.error + ")");
  showNotice("");
  messages = mergeHistory(res.messages ?? [], pendingEvents);
  pendingEvents = [];
  renderLog();
  log.scrollTop = log.scrollHeight;
}

function schedulePoll() {
  clearInterval(pollTimer);
  pollTimer = null;
  if (channelId && !socketUp) pollTimer = setInterval(loadMessages, POLL_MS);
}

let current = null; // {state, channelId}
let sending = false;

function showComposerError(code) {
  composerError.textContent = code ? errorText(code) : "";
  composerError.hidden = !code;
}

function updateComposer(state, puzzle) {
  const show = canCompose(state, puzzle);
  if (composer.hidden === show) showComposerError(null);
  composer.hidden = !show;
}

async function sendDraft() {
  if (sending || !channelId) return;
  const v = validate(draft.value);
  if (!v.ok) return showComposerError(v.code === "empty" ? null : v.code);
  showComposerError(null);
  const target = channelId;
  const sentDraft = draft.value;
  sending = true;
  sendBtn.disabled = true;
  try {
    const res = await apiSend("/api/channels/" + encodeURIComponent(target) + "/messages", "POST", { content: v.content });
    if (target !== channelId) return;
    if (res.authRequired) return setState({ state: "auth" });
    if (res.error) return showComposerError(res.error);
    draft.value = draftAfterSend(draft.value, sentDraft);
  } finally {
    sending = false;
    sendBtn.disabled = false;
  }
}

composer.addEventListener("submit", (e) => {
  e.preventDefault();
  sendDraft();
});
draft.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    sendDraft();
  }
});

async function setState({ state, puzzle, channelNames: names }) {
  currentPuzzle = puzzle ?? null;
  channelNames = names ?? {};
  const next = { state, channelId: targetChannel(state, puzzle) };
  const same = sameTarget(current, next);
  current = next;
  renderHead(puzzle, state);
  updateComposer(state, puzzle);
  if (same) {
    renderLog(); // names may have arrived
    return;
  }
  channelId = next.channelId;
  loadSeq++; // target changed: discard any pending history load for the old one
  const stateSeq = ++setStateSeq;
  pendingEvents = [];
  draft.value = "";
  showComposerError(null);
  messages = [];
  log.replaceChildren();
  if (state === "loading") showNotice("Loading\u2026");
  else if (state === "not_puzzle") showNotice("Not a puzzle sheet.");
  else if (state === "no_channel") showNotice("This puzzle has no Discord channel.");
  else if (state === "error") {
    const origin = await getOrigin();
    const ok = await chrome.permissions.contains({ origins: [origin + "/*"] }).catch(() => true);
    if (stateSeq !== setStateSeq) return;
    showNotice(ok ? "Could not reach the dashboard. Check the extension options." : "Grant access to the dashboard in the extension Options.");
  } else if (state === "unavailable") showNotice("Chat unavailable \u2014 open in Discord.", puzzle?.discord_url);
  else if (state === "auth") {
    const origin = await getOrigin();
    if (stateSeq !== setStateSeq) return;
    showNotice("Log in to the dashboard", origin);
  } else showNotice("");
  schedulePoll();
  if (state === "chat") loadMessages();
}

function renderHead(puzzle, state) {
  head.textContent = puzzle ? puzzle.name + (puzzle.solved && !puzzle.solution ? " (solved)" : "") : "";
  if (puzzle?.solved && puzzle.solution) head.append(el("span", "chip-ok", "✓ Solved · " + puzzle.solution));
  if (puzzle?.discord_url && state === "chat") {
    const a = document.createElement("a");
    a.href = puzzle.discord_url;
    a.target = "_blank";
    a.rel = "noopener";
    a.textContent = "(Discord)";
    head.append(" ", a);
  }
}

let port = null;
let windowId = null;

function post(msg) {
  if (!port || windowId === null) return;
  try {
    port.postMessage({ ...msg, windowId });
  } catch {
    /* disconnected; onDisconnect reconnects */
  }
}

const askState = () => post({ type: "get" });

// ---- unread list and peek bar ----
// The background sends {type: "unread", huntUid, rows, totals, undo, peek}; rows are
// the hunt's chats with news, newest first, without the one this panel shows.
const OPEN_KEY = "unreadListOpen";
let unreadMsg = { huntUid: null, rows: [], totals: { puzzles: 0, messages: 0, solved: 0 }, undo: 0, peek: null };
let listOpen = false;
let listPage = 0;
let topChannel = null; // first row's channel: a new arrival at the top returns to page 1
let lastMessages = 0;
let peeking = null; // {huntUid, channelId} while this panel shows a chat that is not the tab's

chrome.storage.local
  .get({ [OPEN_KEY]: false })
  .then((r) => {
    listOpen = r[OPEN_KEY] === true;
    renderUnread(false);
  })
  .catch(() => {});

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
}

function button(cls, text, onClick, label) {
  const b = el("button", cls, text);
  b.type = "button";
  if (label) b.setAttribute("aria-label", label);
  b.addEventListener("click", onClick);
  return b;
}

function ago(iso) {
  const t = iso ? Date.parse(iso) : NaN;
  if (Number.isNaN(t)) return "";
  const m = Math.max(0, Math.floor((Date.now() - t) / 60000));
  return m < 1 ? "now" : m < 60 ? m + "m" : Math.floor(m / 60) + "h";
}

const plural = (n, one, many) => n + " " + (n === 1 ? one : many);
const peekAt = (ch) => post({ type: "peek", huntUid: unreadMsg.huntUid, channelId: ch.channel_id });
const goTo = (huntUid, channelId) => post({ type: "goto", huntUid, channelId });

function renderRow(ch) {
  const li = el("li", ch.newly_solved ? "solved" : "");
  const row = el("button", "u-row");
  row.type = "button";
  row.title = "Click to look · Ctrl/⌘-click to go to its tab";
  const name = el("span", "u-name", ch.name);
  if (ch.rounds?.length) name.append(el("span", "u-round", ch.rounds.join(", ")));
  const prev = el("span", "u-prev");
  appendInline(prev, rowText(ch));
  row.append(el("span", "badge", ch.newly_solved ? "✓" : ch.unread > 99 ? "99+" : String(ch.unread)), name, el("span", "u-time", ago(ch.last?.created_at)), el("span"), prev);
  row.addEventListener("click", (e) => (e.ctrlKey || e.metaKey ? goTo(unreadMsg.huntUid, ch.channel_id) : peekAt(ch)));
  row.addEventListener("mousedown", (e) => {
    if (e.button === 1) e.preventDefault(); // no autoscroll: middle-click goes to the tab
  });
  row.addEventListener("auxclick", (e) => {
    if (e.button !== 1) return;
    e.preventDefault();
    goTo(unreadMsg.huntUid, ch.channel_id);
  });
  li.append(row, button("u-go", "↗", () => goTo(unreadMsg.huntUid, ch.channel_id), "Go to " + ch.name));
  return li;
}

function spacerRow() {
  const li = el("li", "u-spacer");
  const row = el("div", "u-row");
  row.append(el("span", "badge", "0"), el("span", "u-name", "–"), el("span", "u-time", "–"), el("span"), el("span", "u-prev", "–"));
  li.append(row);
  return li;
}

function renderUnread(fresh) {
  const { huntUid, rows, totals: t, undo } = unreadMsg;
  unreadBox.replaceChildren();
  unreadBox.classList.toggle("open", listOpen);
  if (!huntUid || (!rows.length && !undo)) {
    unreadBox.hidden = true;
    return;
  }
  unreadBox.hidden = false;
  if (!rows.length) {
    const done = el("div", "u-done");
    done.append(el("span", null, "Marked " + plural(undo, "chat", "chats") + " read"), button("u-mark", "Undo", () => post({ type: "undoMarkAll" })));
    unreadBox.append(done);
    return;
  }
  const toggle = button("u-toggle" + (fresh && !listOpen ? " fresh" : ""), null, () => {
    listOpen = !listOpen;
    chrome.storage.local.set({ [OPEN_KEY]: listOpen }).catch(() => {});
    renderUnread(false);
  });
  toggle.setAttribute("aria-expanded", String(listOpen));
  const label = el("span");
  label.append("New in ", el("b", null, String(t.puzzles)), t.puzzles === 1 ? " puzzle · " : " puzzles · ", el("b", null, String(t.messages)));
  toggle.append(el("span", "chev", "▸"), label);
  if (t.solved) toggle.append(el("span", "chip-ok", "✓ " + t.solved + " solved"));
  unreadBox.append(toggle);
  if (!listOpen) return;
  const p = paginate(rows, listPage);
  listPage = p.page;
  const ul = el("ul", "u-list");
  for (const ch of p.slice) ul.append(renderRow(ch));
  for (let i = 0; i < p.pad; i++) ul.append(spacerRow());
  const foot = el("div", "u-foot");
  foot.append(button("u-mark", "✓ Mark all read", () => post({ type: "markAllRead" })));
  if (p.pages > 1) {
    const pager = el("div", "u-pager");
    const prev = button(null, "‹", () => {
      listPage--;
      renderUnread(false);
    }, "Previous page");
    const next = button(null, "›", () => {
      listPage++;
      renderUnread(false);
    }, "Next page");
    prev.disabled = p.page === 0;
    next.disabled = p.page === p.pages - 1;
    pager.append(prev, el("span", null, p.from + "–" + p.to + " of " + p.total), next);
    foot.append(pager);
  }
  unreadBox.append(ul, foot);
}

function renderPeek(peek) {
  peekBar.replaceChildren();
  peekBar.hidden = !peek;
  if (!peek) return;
  const back = button(null, "← Back to " + (peek.name ?? "your tab"), () => post({ type: "peek", huntUid: null, channelId: null }));
  if (peek.unread > 0) back.append(el("span", "badge", peek.unread > 99 ? "99+" : String(peek.unread)));
  peekBar.append(
    el("span", "why", "Looking at a different chat from your tab"),
    button("primary", "Go to puzzle ↗", () => currentPuzzle && goTo(currentPuzzle.hunt_uid, currentPuzzle.channel_id)),
    back,
  );
}

function onUnread(msg) {
  const rows = Array.isArray(msg.rows) ? msg.rows : [];
  if ((rows[0]?.channel_id ?? null) !== topChannel) {
    topChannel = rows[0]?.channel_id ?? null;
    listPage = 0;
  }
  const messagesNow = msg.totals?.messages ?? 0;
  const fresh = messagesNow > lastMessages;
  lastMessages = messagesNow;
  unreadMsg = { huntUid: msg.huntUid ?? null, rows, totals: msg.totals ?? unreadMsg.totals, undo: msg.undo ?? 0, peek: msg.peek ?? null };
  renderUnread(fresh);
  renderPeek(unreadMsg.peek);
}

// Ages ("4m") go stale while nothing arrives.
setInterval(() => {
  if (listOpen && !unreadBox.hidden) renderUnread(false);
}, 60000);

function connectPort() {
  port = chrome.runtime.connect({ name: "panel" });
  port.onMessage.addListener((msg) => {
    if (msg.type === "puzzle") {
      peeking = msg.peek && msg.puzzle ? { huntUid: msg.puzzle.hunt_uid, channelId: msg.puzzle.channel_id } : null;
      renderPeek(msg.peek ?? null);
      setState(msg);
    } else if (msg.type === "unread") {
      onUnread(msg);
    } else if (msg.type === "reset") {
      // Dashboard origin changed or was revoked: drop history and composer, then ask afresh.
      peeking = null;
      onUnread({ huntUid: null, rows: [], undo: 0, peek: null });
      setState({ state: "loading" });
      askState();
    } else if (msg.type === "names") {
      if (currentPuzzle?.hunt_uid === msg.huntUid) {
        channelNames = { ...channelNames, ...msg.channelNames };
        renderLog();
      }
    } else if (msg.type === "socket") {
      const was = socketUp;
      socketUp = msg.connected;
      schedulePoll();
      if (!was && socketUp) loadMessages();
    } else if (msg.type === "event" && channelId && eventChannel(msg.event) === channelId) {
      messages = applyEvent(messages, msg.event);
      pendingEvents.push(msg.event);
      if (pendingEvents.length > 500) pendingEvents.shift();
      renderLog();
    }
  });
  port.onDisconnect.addListener(() => {
    port = null;
    socketUp = false;
    schedulePoll();
    setTimeout(() => {
      const keep = peeking; // a restarted worker forgot the peek: ask for it again
      connectPort();
      askState();
      if (keep) post({ type: "peek", ...keep });
    }, 1000);
  });
}

chrome.windows.getCurrent().then((w) => {
  windowId = w.id;
  connectPort();
  askState();
});
// Only this window's tab switches: one elsewhere must not end this panel's peek.
chrome.tabs.onActivated.addListener((info) => {
  if (info.windowId === windowId) askState();
});
