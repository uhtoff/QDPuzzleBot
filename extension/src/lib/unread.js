// Unread counts per Discord channel. `isTracked(channelId)` says whether any
// puzzle tab is open for the channel; `visible` is the channel ids shown in
// open panels (array/Set) -- those are being read, so not counted.
export function createUnread({ isTracked, initial = [] } = {}) {
  const counts = new Map(initial);
  const api = {
    onMessage(channelId, visible = []) {
      if (!channelId || !isTracked(channelId)) return false;
      if (new Set(visible).has(channelId)) return false;
      counts.set(channelId, (counts.get(channelId) ?? 0) + 1);
      return true;
    },
    onEvent: (ev, visible) => ev?.type === "message" && api.onMessage(ev.channel_id == null ? null : String(ev.channel_id), visible),
    clear: (channelId) => void counts.delete(channelId),
    count: (channelId) => counts.get(channelId) ?? 0,
    total: () => [...counts.values()].reduce((a, b) => a + b, 0),
    serialise: () => [...counts],
  };
  return api;
}

export const badgeText = (total) => (total <= 0 ? "" : total > 99 ? "99+" : String(total));
