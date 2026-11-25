// The click target is encoded in the notification id so it survives worker eviction:
// a tab ("tab:...") for a chat message, or a hunt's chat ("chat:...") for a solve,
// which may have no tab open.
export const notificationId = (tabId, windowId, now = Date.now()) => `tab:${tabId}:${windowId}:${now}`;
export const chatNotificationId = (huntUid, channelId, windowId, now = Date.now()) =>
  `chat:${encodeURIComponent(huntUid)}:${channelId}:${windowId}:${now}`;

export function parseNotificationId(id) {
  const s = String(id ?? "");
  const t = /^tab:(\d+):(\d+):(\d+)$/.exec(s);
  if (t) return { tabId: Number(t[1]), windowId: Number(t[2]) };
  const c = /^chat:([^:]+):(\d+):(\d+):(\d+)$/.exec(s);
  if (!c) return null;
  try {
    return { huntUid: decodeURIComponent(c[1]), channelId: c[2], windowId: Number(c[3]) };
  } catch {
    return null; // a bad escape
  }
}

const KIND_HUMAN = 0;
const KIND_THIS_BOT = 1;

const FEEDER_SOLVED = /^✅ (.+) solved: /;
const isSolvedEmbed = (message) => Array.isArray(message.embeds) && message.embeds.some((e) => /SOLVED!/.test(String(e?.title ?? "")));

// Which desktop notification a message can raise: "message" for people, "solve" for
// this bot's solve announcements (feeder "✅ <#id> solved: ..." or a "...SOLVED!"
// embed), else null.
export function notifyKind(message) {
  const kind = message?.kind;
  if (kind === KIND_HUMAN) return "message";
  if (kind !== KIND_THIS_BOT) return null;
  if (FEEDER_SOLVED.test(String(message.content ?? "")) || isSolvedEmbed(message)) return "solve";
  return null;
}

// The puzzle a solve announcement is about, as a channel id: a solved embed is posted
// in the puzzle's own channel, a feeder line in each meta it feeds (linking the feeder).
// One solve can so arrive several times; the background notifies once per puzzle.
// Null for anything else, and for a feeder with no channel (named in bold).
export function solvedChannel(message, channelId) {
  if (notifyKind(message) !== "solve") return null;
  const m = FEEDER_SOLVED.exec(String(message.content ?? ""));
  if (m) return /^<#(\d+)>$/.exec(m[1])?.[1] ?? null;
  return channelId == null ? null : String(channelId);
}

// The two switches on the Options page. Before there were two, one "notify" switch
// covered both: it carries over to whichever has not been set since.
export function notifyPrefs(stored) {
  const old = !!stored?.notify;
  const pick = (v) => (typeof v === "boolean" ? v : old);
  return { messages: pick(stored?.notifyMessages), solves: pick(stored?.notifySolves) };
}
