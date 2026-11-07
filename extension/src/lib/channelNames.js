// Per-hunt channel_id -> puzzle name cache. `fetcher(huntUid)` resolves to a
// {channelId: name} object or throws. Injected clock for tests.
export function createNameCache({ fetcher, now = Date.now, ttl = 60000, refetchGap = 30000 }) {
  const hunts = new Map(); // huntUid -> { map, at, tried }
  async function ensure(huntUid, wantId) {
    if (!huntUid) return {};
    const e = hunts.get(huntUid);
    const t = now();
    const expired = !e || t - e.at >= ttl;
    const missing = e && wantId != null && !(wantId in e.map) && t - e.tried >= refetchGap;
    if (e && !expired && !missing) return e.map;
    if (e && expired && t - e.tried < refetchGap) return e.map; // expired but just failed
    try {
      const map = await fetcher(huntUid);
      hunts.set(huntUid, { map, at: now(), tried: now() });
      return map;
    } catch {
      const keep = e?.map ?? {};
      hunts.set(huntUid, { map: keep, at: e?.at ?? -Infinity, tried: now() });
      return keep;
    }
  }
  return { ensure };
}

// Hunt map first, then open tabs (registry), then "#channel" (caller's default).
export const resolveName = (id, huntMap, registryName) => huntMap?.[id] || registryName?.(id) || null;
