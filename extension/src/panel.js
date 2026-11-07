import { messageView, isBangCommand } from "./lib/format.js";
import { applyEvent, eventChannel } from "./lib/panelState.js";
import { messagesPath, safeAvatarUrl } from "./lib/origin.js";
import { apiGet, getOrigin } from "./api.js";

const KIND_COMMAND = 3;
const POLL_MS = 15000;
const head = document.getElementById("head");
const notice = document.getElementById("notice");
const log = document.getElementById("log");

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
  text.textContent = v.text;
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

async function setState({ state, puzzle }) {
  channelId = state === "chat" ? puzzle.channel_id : null;
  messages = [];
  log.replaceChildren();
  head.textContent = puzzle ? puzzle.name : "";
  if (puzzle?.discord_url && state === "chat") {
    const a = document.createElement("a");
    a.href = puzzle.discord_url;
    a.target = "_blank";
    a.rel = "noopener";
    a.textContent = " (Discord)";
    head.append(a);
  }
  if (state === "not_puzzle") showNotice("Not a puzzle sheet.");
  else if (state === "no_channel") showNotice("This puzzle has no Discord channel.");
  else if (state === "error") showNotice("Could not reach the dashboard. Check the extension options.");
  else if (state === "unavailable") showNotice("Chat unavailable — open in Discord.", puzzle?.discord_url);
  else if (state === "auth") showNotice("Log in to the dashboard", await getOrigin());
  else showNotice("");
  schedulePoll();
  if (state === "chat") loadMessages();
}

const port = chrome.runtime.connect({ name: "panel" });
port.onMessage.addListener((msg) => {
  if (msg.type === "puzzle") {
    setState(msg);
  } else if (msg.type === "socket") {
    socketUp = msg.connected;
    schedulePoll();
  } else if (msg.type === "event" && channelId && eventChannel(msg.event) === channelId) {
    messages = applyEvent(messages, msg.event);
    renderLog();
  }
});
port.postMessage({ type: "get" });
chrome.tabs.onActivated.addListener(() => port.postMessage({ type: "get" }));
