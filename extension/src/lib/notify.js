// The click target is encoded in the notification id so it survives worker eviction.
export const notificationId = (tabId, windowId, now = Date.now()) => `tab:${tabId}:${windowId}:${now}`;

export function parseNotificationId(id) {
  const m = /^tab:(\d+):(\d+):(\d+)$/.exec(String(id ?? ""));
  return m ? { tabId: Number(m[1]), windowId: Number(m[2]) } : null;
}
