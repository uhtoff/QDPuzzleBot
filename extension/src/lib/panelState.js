import { parseSheetUrl } from "./sheetUrl.js";

// Is the cached registry entry still the answer for this URL?
export function cacheValid(entry, parsed) {
  return !!entry && !!parsed && entry.spreadsheetId === parsed.spreadsheetId && (entry.gid ?? null) === (parsed.gid ?? null);
}

// Panel state name for a tab. `entry` is the stored puzzle (or undefined);
// `status` is the outcome of a failed lookup: "auth" | "error" | "notfound" | undefined.
export function stateFor(entry, status) {
  if (entry) {
    if (entry.bridge_enabled === false) return "unavailable";
    if (!entry.channel_id) return "no_channel";
    return "chat";
  }
  if (status === "auth") return "auth";
  if (status === "error") return "error";
  return "not_puzzle";
}

export function isPuzzleUrl(url) {
  return !!url && parseSheetUrl(url) !== null;
}

// Which channel does a websocket event concern? null for events to ignore.
export function eventChannel(ev) {
  if (!ev || !["message", "message_edit", "message_delete"].includes(ev.type)) return null;
  return ev.channel_id ? String(ev.channel_id) : null;
}

// Apply a message event to an oldest-first message list, returning a new list.
// Ids are snowflake strings.
export function applyEvent(messages, ev) {
  if (ev.type === "message") {
    if (messages.some((m) => m.id === ev.message.id)) return messages;
    return [...messages, ev.message];
  }
  if (ev.type === "message_edit") {
    return messages.map((m) => (m.id === ev.message.id ? { ...m, ...ev.message, edited_at: m.edited_at ?? new Date().toISOString() } : m));
  }
  if (ev.type === "message_delete") {
    return messages.filter((m) => m.id !== ev.message_id);
  }
  return messages;
}

// Should a snapshot for `tab` be posted to a panel living in `portWindowId`?
// Only the active tab of the panel's own window counts.
export function shouldDeliver(portWindowId, tab) {
  return !!tab && !!tab.active && tab.windowId === portWindowId;
}

// Is `next` the same render target as `prev` ({state, channelId})? If so the
// panel must not clear and refetch the chat.
export function sameTarget(prev, next) {
  return !!prev && !!next && prev.state === next.state && (prev.channelId ?? null) === (next.channelId ?? null);
}

const idAfter = (a, b) => {
  try {
    return BigInt(a) > BigInt(b);
  } catch {
    return Number(a) > Number(b);
  }
};

// Combine a history response (oldest-first) with live state that moved while it
// was in flight: keep current messages newer than the response's newest, then
// replay the events seen meanwhile (edits, deletes, new messages). Deduped by id.
export function mergeHistory(response, current, pendingEvents = []) {
  let out = [...response];
  const newest = out.length ? out[out.length - 1].id : null;
  for (const m of current) {
    if (out.some((x) => x.id === m.id)) continue;
    if (newest === null || idAfter(m.id, newest)) out.push(m);
  }
  for (const ev of pendingEvents) out = applyEvent(out, ev);
  return out;
}
