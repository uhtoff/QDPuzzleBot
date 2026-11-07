// Discord markup helpers. Pure: plain strings and plain data only, no HTML.

// `channelName(id)` returns the puzzle name for a channel id, or a falsy value.
export function rewriteMentions(text, channelName = () => null) {
  return String(text ?? "")
    .replace(/<#(\d+)>/g, (_, id) => "#" + (channelName(id) || "channel"))
    .replace(/<@&\d+>/g, "@role")
    .replace(/<@!?\d+>/g, "@someone")
    .replace(/<a?:(\w+):\d+>/g, ":$1:");
}

export const stripMarkers = (text) => String(text).replace(/\*\*|`/g, "");

// Split into [{text, bold?, code?}] segments. `code` spans take precedence;
// unmatched markers stay literal.
export function tokenise(text) {
  const s = String(text ?? "");
  const out = [];
  const re = /`([^`\n]+)`|\*\*(.+?)\*\*/gs;
  let last = 0;
  let m;
  while ((m = re.exec(s))) {
    if (m.index > last) out.push({ text: s.slice(last, m.index) });
    out.push(m[1] !== undefined ? { text: m[1], code: true } : { text: m[2], bold: true });
    last = re.lastIndex;
  }
  if (last < s.length) out.push({ text: s.slice(last) });
  return out;
}

// Notification body: "<author>: <text>", cleaned, truncated to ~120 chars.
export function notificationText(message, channelName) {
  let body = String(message?.content ?? "");
  if (!body && Array.isArray(message?.embeds)) {
    body = message.embeds
      .map((e) => [e?.title, e?.description].filter(Boolean).join(" "))
      .filter(Boolean)
      .join(" ");
  }
  const text = `${message?.author_name ?? ""}: ${stripMarkers(rewriteMentions(body, channelName))}`;
  return text.length > 120 ? text.slice(0, 119) + "…" : text;
}
