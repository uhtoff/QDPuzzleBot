import { maxId } from "./readMarkers.js";

// The unread list: channel entries as POST /api/hunts/<uid>/unread returns them
// ({channel_id, kind, puzzle_id, name, rounds, solved, solution, newly_solved,
// solved_at, sheet_url, discord_url, unread, last}), plus `solved_seq`, the snowflake of when
// the background first saw a channel as newly solved (the server sends no solve time).

export const PER_PAGE = 5;
const KIND_HUMAN = 0;

// A solved chat's unread messages stay listed this long after the solve, so late
// chatter (backsolve notes, tags) is still seen; after that a solve silences it.
export const SOLVED_GRACE_MS = 10 * 60000;

// When a solved chat's window closes (ms), or null when its solve time is unknown.
function graceEnd(ch) {
  const t = ch.solved && ch.solved_at ? Date.parse(ch.solved_at) : NaN;
  return Number.isNaN(t) ? null : t + SOLVED_GRACE_MS;
}

const inGrace = (ch, now) => {
  const end = graceEnd(ch);
  return end !== null && now < end;
};

export const isListed = (ch, now = Date.now()) =>
  (ch.unread > 0 && (!ch.solved || inGrace(ch, now))) || !!ch.newly_solved;

// The soonest moment a row listed only by the grace window drops out, or null:
// the background re-sends the list then, as nothing else may change meanwhile.
export function nextGraceEnd(channels, now = Date.now()) {
  let soonest = null;
  for (const ch of channels) {
    if (!(ch.unread > 0) || ch.newly_solved || !inGrace(ch, now)) continue;
    const end = graceEnd(ch);
    if (soonest === null || end < soonest) soonest = end;
  }
  return soonest;
}

// Sort key: the later of its last message and its solve.
export const activityId = (ch) => maxId(ch.last?.id, ch.solved_seq) ?? "0";

// Listed chats, newest activity first, without the one the panel is showing.
export function listRows(channels, viewingChannelId, now = Date.now()) {
  return channels
    .filter((ch) => isListed(ch, now) && ch.channel_id !== viewingChannelId)
    .sort((a, b) => {
      const x = BigInt(activityId(a));
      const y = BigInt(activityId(b));
      return x === y ? 0 : x > y ? -1 : 1;
    });
}

// One page of rows. `pad` keeps a short last page five rows tall so the chat below
// does not jump; a single page needs none.
export function paginate(rows, page) {
  const total = rows.length;
  const pages = Math.max(1, Math.ceil(total / PER_PAGE));
  const p = Math.min(Math.max(0, Math.floor(page) || 0), pages - 1);
  const start = p * PER_PAGE;
  const slice = rows.slice(start, start + PER_PAGE);
  return {
    page: p,
    pages,
    from: total ? start + 1 : 0,
    to: start + slice.length,
    total,
    slice,
    pad: pages > 1 ? PER_PAGE - slice.length : 0,
  };
}

export function totals(rows) {
  return {
    puzzles: rows.length,
    messages: rows.reduce((n, ch) => n + (ch.unread || 0), 0),
    solved: rows.filter((ch) => ch.newly_solved).length,
  };
}

const lastOf = (m) => ({ id: String(m.id), author_name: m.author_name ?? "", content: m.content ?? "", created_at: m.created_at ?? null });

// Fold a live `message` event into one hunt's channels. `known` is false when the
// channel is not in this hunt (the caller may refetch). `markers` (optional) skips
// a message already read; an id at or below `last.id` was already counted.
export function applyMessage(channels, ev, { visible = [], markers = null, selfId = null } = {}) {
  const cid = ev?.channel_id == null ? null : String(ev.channel_id);
  const i = channels.findIndex((ch) => ch.channel_id === cid);
  if (i < 0) return { channels, known: false };
  const m = ev.message;
  if (!m || m.kind !== KIND_HUMAN) return { channels, known: true };
  const ch = channels[i];
  if (ch.last?.id && BigInt(m.id) <= BigInt(ch.last.id)) return { channels, known: true };
  const read = visible.includes(cid) || (selfId != null && String(m.author_id) === String(selfId));
  const counts = !read && (!markers || markers.isUnread(cid, String(m.id)));
  const next = { ...ch, last: lastOf(m), unread: read ? 0 : ch.unread + (counts ? 1 : 0) };
  const out = [...channels];
  out[i] = next;
  return { channels: out, known: true };
}

const cleared = (ch) => ({ ...ch, unread: 0, newly_solved: false });
export const clearChannel = (channels, channelId) => channels.map((ch) => (ch.channel_id === channelId ? cleared(ch) : ch));
export const clearAll = (channels) => channels.map(cleared);

// The toolbar badge: unread in chats that have a tab open.
export function badgeCount(channels, trackedChannelIds) {
  const tracked = new Set(trackedChannelIds);
  return channels.reduce((n, ch) => n + (tracked.has(ch.channel_id) ? ch.unread || 0 : 0), 0);
}

export const badgeText = (total) => (total <= 0 ? "" : total > 99 ? "99+" : String(total));

// The row's second line.
export function rowText(ch) {
  if (ch.newly_solved) return ch.solution ? "Solved: " + ch.solution : "Solved";
  if (!ch.last) return "";
  const text = String(ch.last.content ?? "").replace(/\s+/g, " ").trim();
  return (ch.last.author_name ?? "") + ": " + text;
}

// A peeked chat in the by-sheet lookup's shape, so the panel renders it like any other.
export function peekPuzzle(ch, huntUid) {
  return {
    id: ch.puzzle_id ?? null,
    name: ch.name,
    solved: !!ch.solved,
    solution: ch.solution ?? null,
    channel_id: ch.channel_id,
    discord_url: ch.discord_url ?? null,
    sheet_url: ch.sheet_url ?? null,
    hunt_uid: huntUid,
    hunt_archived: false,
    sheet_mode: null,
    hunt_chat: ch.kind === "general",
    bridge_enabled: true,
  };
}
