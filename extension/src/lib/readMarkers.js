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

// How far the server's clock is ahead of this machine's (ms), from a response's
// `server_time_ms` and the local times its request was sent and answered; null when
// the response carries none. Markers and solve times are compared on the server, so
// "now" must be the server's now: a fast local clock would otherwise mark messages
// and solves of the next few minutes as already read.
export function clockSkew(serverMs, sentMs, receivedMs) {
  if (typeof serverMs !== "number" || !Number.isFinite(serverMs)) return null;
  return Math.round(serverMs - (sentMs + receivedMs) / 2);
}

// `initial` is a stored {baseline, read, provisional_ms?}; anything malformed in it
// is dropped.
export function createMarkers(initial) {
  let baseline = isId(initial?.baseline) ? initial.baseline : null;
  // The local moment a first-run baseline was taken at, until the server's clock
  // corrects it (settleBaseline). Null once settled, or for a baseline from before.
  let provisionalMs = baseline && Number.isFinite(initial?.provisional_ms) ? initial.provisional_ms : null;
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
      provisionalMs = nowMs;
      return true;
    },
    // The first response's clock skew: move a first-run baseline taken from this
    // machine's clock to the server's. Once; true when it moved. A baseline moved
    // since (Mark all read) is left as it is.
    settleBaseline(skewMs) {
      if (provisionalMs === null) return false;
      const at = provisionalMs;
      provisionalMs = null;
      if (baseline !== snowflakeAt(at)) return false;
      const next = snowflakeAt(at + skewMs);
      if (next === baseline) return false;
      baseline = next;
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
    // What to persist: every marker that still says something (above the baseline).
    // Not request(): its cap is the server's limit on one request, and storing the
    // capped set would forget the older markers for good.
    stored() {
      const base = baseline ?? "0";
      const out = { baseline: base, read: Object.fromEntries(Object.entries(read).filter(([, id]) => BigInt(id) > BigInt(base))) };
      if (provisionalMs !== null) out.provisional_ms = provisionalMs;
      return out;
    },
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
