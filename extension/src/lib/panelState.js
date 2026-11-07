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
