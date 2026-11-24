// Read markers for the unread list. A marker is a Discord message id (a snowflake,
// which sorts by time) as a string: one `baseline` for every channel plus
// per-channel `read` markers. Snowflakes outgrow Number, so compare with BigInt.

export const DISCORD_EPOCH_MS = 1420070400000n;
// The server refuses more read markers than this (bot/web/unread.py MAX_READ_ENTRIES).
export const MAX_READ = 500;

const isId = (v) => typeof v === "string" && /^\d+$/.test(v);

// The snowflake for a moment, as a string.
export const snowflakeAt = (ms) => String((BigInt(Math.floor(ms)) - DISCORD_EPOCH_MS) << 22n);

// The largest id given, ignoring empty values; null when none is left.
export function maxId(...ids) {
  let best = null;
  for (const id of ids) {
    if (id === null || id === undefined || id === "") continue;
    if (best === null || BigInt(id) > BigInt(best)) best = String(id);
  }
  return best;
}

// `initial` is a stored {baseline, read}; anything malformed in it is dropped.
export function createMarkers(initial) {
  let baseline = isId(initial?.baseline) ? initial.baseline : null;
  let read = {};
  for (const [cid, id] of Object.entries(initial?.read ?? {})) if (isId(id)) read[cid] = id;

  const effective = (channelId) => maxId(baseline, read[channelId]) ?? "0";
  const state = () => ({ baseline, read: { ...read } });

  return {
    state,
    effective,
    // First run: start from now, so history never shows as new. True if it set one.
    ensureBaseline(nowMs) {
      if (baseline) return false;
      baseline = snowflakeAt(nowMs);
      return true;
    },
    // The chat was seen: read up to its newest message, or to now if that is later
    // (now is what clears a solve that no message followed).
    look(channelId, newestId, nowMs) {
      const next = maxId(effective(channelId), newestId, snowflakeAt(nowMs));
      if (next === effective(channelId)) return false;
      read[channelId] = next;
      return true;
    },
    // Returns the state it replaced, for Undo.
    markAll(nowMs) {
      const prev = state();
      baseline = maxId(baseline, snowflakeAt(nowMs));
      read = {};
      return prev;
    },
    restore(prev) {
      baseline = isId(prev?.baseline) ? prev.baseline : baseline;
      read = { ...(prev?.read ?? {}) };
    },
    isUnread: (channelId, messageId) => BigInt(messageId) > BigInt(effective(channelId)),
    // What the server needs: entries at or below the baseline add nothing. Limit to
    // `channelIds` (one hunt's chats) when known; past the server's cap keep the
    // newest, since an older marker matters least.
    request(channelIds) {
      const base = baseline ?? "0";
      const only = channelIds ? new Set(channelIds) : null;
      let entries = Object.entries(read).filter(([cid, id]) => (!only || only.has(cid)) && BigInt(id) > BigInt(base));
      if (entries.length > MAX_READ) {
        entries.sort(([, a], [, b]) => (BigInt(a) > BigInt(b) ? -1 : BigInt(a) < BigInt(b) ? 1 : 0));
        entries = entries.slice(0, MAX_READ);
      }
      return { baseline: base, read: Object.fromEntries(entries) };
    },
  };
}
