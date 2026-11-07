import { replaceShortcodes } from "./emoji.js";

// Discord markup helpers. Pure: plain strings and plain data only, no HTML.

// `channelName(id)` returns the puzzle name for a channel id, or a falsy value.
export function rewriteMentions(text, channelName = () => null, { keepCustomEmoji = false } = {}) {
  const out = String(text ?? "")
    .replace(/<#(\d+)>/g, (_, id) => "#" + (channelName(id) || "channel"))
    .replace(/<@&\d+>/g, "@role")
    .replace(/<@!?\d+>/g, "@someone");
  return keepCustomEmoji ? out : out.replace(/<a?:(\w+):\d+>/g, ":$1:");
}

export const stripMarkers = (text) => String(text).replace(/\*\*|`/g, "");

// Split into segments: {text}, {text, bold}, {text, code} or
// {emoji: {name, id, animated}, text: ":name:"}. Code spans are literal (no
// shortcode replacement); unmatched markers stay literal. Custom emoji only
// survive rewriteMentions when called with keepCustomEmoji.
export function tokenise(text) {
  const s = String(text ?? "");
  const out = [];
  const re = /`([^`\n]+)`|\*\*(.+?)\*\*|<(a?):(\w+):(\d+)>/gs;
  const plain = (t) => t && out.push({ text: replaceShortcodes(t) });
  let last = 0;
  let m;
  while ((m = re.exec(s))) {
    plain(s.slice(last, m.index));
    if (m[1] !== undefined) out.push({ text: m[1], code: true });
    else if (m[2] !== undefined) out.push({ text: replaceShortcodes(m[2]), bold: true });
    else out.push({ text: `:${m[4]}:`, emoji: { name: m[4], id: m[5], animated: m[3] === "a" } });
    last = re.lastIndex;
  }
  plain(s.slice(last));
  return out;
}

export const emojiUrl = ({ id, animated }) => `https://cdn.discordapp.com/emojis/${id}.${animated ? "gif" : "png"}?size=32`;

// An embed as display lines: [{kind: "title"|"description"|"field"|"footer", text}].
export function embedLines(embed) {
  const out = [];
  if (embed?.title) out.push({ kind: "title", text: String(embed.title) });
  if (embed?.description) out.push({ kind: "description", text: String(embed.description) });
  for (const f of embed?.fields ?? []) {
    const t = [f?.name, f?.value].filter(Boolean).join(": ");
    if (t) out.push({ kind: "field", text: t });
  }
  if (embed?.footer) out.push({ kind: "footer", text: String(embed.footer) });
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
  const text = `${message?.author_name ?? ""}: ${replaceShortcodes(stripMarkers(rewriteMentions(body, channelName)))}`;
  return text.length > 120 ? text.slice(0, 119) + "…" : text;
}
