import { messageView, isBangCommand, errorText } from "./lib/format.js";
import { canCompose, validate } from "./lib/composer.js";
import { applyEvent, eventChannel, sameTarget } from "./lib/panelState.js";
import { messagesPath, safeAvatarUrl } from "./lib/origin.js";
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

let channelId = null;
let messages = [];
let socketUp = false;
let pollTimer = null;
let loadSeq = 0;

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
  if (!v.text && v.hasEmbeds) {
    text.textContent = "[embed]";
    text.classList.add("attach"); // dimmed italic
  } else text.textContent = v.text;
  body.append(meta, text);
  if (Array.isArray(m.attachments) && m.attachments.length) {
    const n = document.createElement("div");
    n.className = "attach";
    n.textContent = "[" + m.attachments.length + " attachment" + (m.attachments.length > 1 ? "s" : "") + "]";
    body.append(n);
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
  const res = await apiGet(messagesPath(channelId));
  if (seq !== loadSeq) return;
  if (res.authRequired) return setState({ state: "auth" });
  if (res.error) return showNotice("Could not load messages (" + res.error + ")");
  showNotice("");
  messages = res.messages ?? [];
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
  sending = true;
  sendBtn.disabled = true;
  try {
    const res = await apiSend("/api/channels/" + encodeURIComponent(target) + "/messages", "POST", { content: v.content });
    if (target !== channelId) return;
    if (res.authRequired) return setState({ state: "auth" });
    if (res.error) return showComposerError(res.error);
    draft.value = "";
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

async function setState({ state, puzzle }) {
  const next = { state, channelId: state === "chat" ? puzzle.channel_id : null };
  const same = sameTarget(current, next);
  current = next;
  renderHead(puzzle, state);
  updateComposer(state, puzzle);
  if (same) return;
  channelId = next.channelId;
  draft.value = "";
  showComposerError(null);
  messages = [];
  log.replaceChildren();
  if (state === "not_puzzle") showNotice("Not a puzzle sheet.");
  else if (state === "no_channel") showNotice("This puzzle has no Discord channel.");
  else if (state === "error") {
    const origin = await getOrigin();
    const ok = await chrome.permissions.contains({ origins: [origin + "/*"] }).catch(() => true);
    showNotice(ok ? "Could not reach the dashboard. Check the extension options." : "Grant access to the dashboard in the extension Options.");
  } else if (state === "unavailable") showNotice("Chat unavailable \u2014 open in Discord.", puzzle?.discord_url);
  else if (state === "auth") showNotice("Log in to the dashboard", await getOrigin());
  else showNotice("");
  schedulePoll();
  if (state === "chat") loadMessages();
}

function renderHead(puzzle, state) {
  head.textContent = puzzle ? puzzle.name + (puzzle.solved ? " (solved)" : "") : "";
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

function askState() {
  if (!port || windowId === null) return;
  try {
    port.postMessage({ type: "get", windowId });
  } catch {
    /* disconnected; onDisconnect reconnects */
  }
}

function connectPort() {
  port = chrome.runtime.connect({ name: "panel" });
  port.onMessage.addListener((msg) => {
    if (msg.type === "puzzle") {
      setState(msg);
    } else if (msg.type === "socket") {
      const was = socketUp;
      socketUp = msg.connected;
      schedulePoll();
      if (!was && socketUp) loadMessages();
    } else if (msg.type === "event" && channelId && eventChannel(msg.event) === channelId) {
      messages = applyEvent(messages, msg.event);
      renderLog();
    }
  });
  port.onDisconnect.addListener(() => {
    port = null;
    socketUp = false;
    schedulePoll();
    setTimeout(() => {
      connectPort();
      askState();
    }, 1000);
  });
}

chrome.windows.getCurrent().then((w) => {
  windowId = w.id;
  connectPort();
  askState();
});
chrome.tabs.onActivated.addListener(askState);
