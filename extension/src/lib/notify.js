// The click target is encoded in the notification id so it survives worker eviction.
export const notificationId = (tabId, windowId, now = Date.now()) => `tab:${tabId}:${windowId}:${now}`;

export function parseNotificationId(id) {
  const m = /^tab:(\d+):(\d+):(\d+)$/.exec(String(id ?? ""));
  return m ? { tabId: Number(m[1]), windowId: Number(m[2]) } : null;
}

const KIND_HUMAN = 0;
const KIND_THIS_BOT = 1;

// Which messages raise a desktop notification: people, and this bot's solve
// announcements (feeder "✅ <#id> solved: ..." or a "...SOLVED!" embed).
export function shouldNotify(message) {
  const kind = message?.kind;
  if (kind === KIND_HUMAN) return true;
  if (kind !== KIND_THIS_BOT) return false;
  if (/^✅ .+ solved: /.test(String(message.content ?? ""))) return true;
  return Array.isArray(message.embeds) && message.embeds.some((e) => /SOLVED!/.test(String(e?.title ?? "")));
}
