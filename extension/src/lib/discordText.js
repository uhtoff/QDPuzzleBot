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

// The url as given when it's http(s), else null: nothing else is ever a link.
export function safeLink(url) {
  try {
    const u = new URL(String(url ?? ""));
    return u.protocol === "https:" || u.protocol === "http:" ? String(url) : null;
  } catch {
    return null;
  }
}

// Trailing punctuation is the sentence's, not the URL's (as on the dashboard).
const URL_TAIL = /[.,:;!?'")\]]+$/;

// Split into segments: {text}, {text, bold}, {text, code}, {text, href} or
// {emoji: {name, id, animated}, text: ":name:"}. Code spans and links are literal
// (no shortcode replacement); unmatched markers stay literal. Links are http(s)
// only: a bare url, Discord's <url> (no preview) or [text](url). Custom emoji only
// survive rewriteMentions when called with keepCustomEmoji.
export function tokenise(text) {
  const s = String(text ?? "");
  const out = [];
  const re = /`([^`\n]+)`|<(https?:\/\/[^\s<>]+)>|\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)|(https?:\/\/[^\s<]+)|\*\*(.+?)\*\*|<(a?):(\w+):(\d+)>/gs;
  const plain = (t) => t && out.push({ text: replaceShortcodes(t) });
  const link = (shown, url) => {
    const href = safeLink(url);
    out.push(href ? { text: shown, href } : { text: shown });
  };
  let last = 0;
  let m;
  while ((m = re.exec(s))) {
    plain(s.slice(last, m.index));
    if (m[1] !== undefined) out.push({ text: m[1], code: true });
    else if (m[2] !== undefined) link(m[2], m[2]);
    else if (m[3] !== undefined) link(m[3], m[4]);
    else if (m[5] !== undefined) {
      const url = m[5].replace(URL_TAIL, "");
      link(url, url);
      re.lastIndex = m.index + url.length;
    } else if (m[6] !== undefined) out.push({ text: replaceShortcodes(m[6]), bold: true });
    else out.push({ text: `:${m[8]}:`, emoji: { name: m[8], id: m[9], animated: m[7] === "a" } });
    last = re.lastIndex;
  }
  plain(s.slice(last));
  return out;
}

export const emojiUrl = ({ id, animated }) => `https://cdn.discordapp.com/emojis/${id}.${animated ? "gif" : "png"}?size=32`;

// An embed as display lines: [{kind: "title"|"description"|"field"|"footer", text}];
// the title also carries href when the embed links somewhere http(s).
export function embedLines(embed) {
  const out = [];
  if (embed?.title) {
    const href = safeLink(embed.url);
    out.push(href ? { kind: "title", text: String(embed.title), href } : { kind: "title", text: String(embed.title) });
  }
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
  // Links as their text: <url> unwrapped, [text](url) as the text.
  body = body.replace(/<(https?:\/\/[^\s<>]+)>/g, "$1").replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, "$1");
  const text = `${message?.author_name ?? ""}: ${replaceShortcodes(stripMarkers(rewriteMentions(body, channelName)))}`;
  return text.length > 120 ? text.slice(0, 119) + "…" : text;
}
