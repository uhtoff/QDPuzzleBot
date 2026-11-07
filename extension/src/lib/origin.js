export const DEFAULT_ORIGIN = "https://quarantinedecrypters.com";

// Accept only a bare https origin (no path/query/hash/credentials). Returns the
// normalised origin string, or null.
export function parseOrigin(input) {
  let u;
  try {
    u = new URL(String(input).trim());
  } catch {
    return null;
  }
  if (u.protocol !== "https:" || u.username || u.password || u.search || u.hash) return null;
  if (u.pathname !== "/") return null;
  return u.origin;
}

export function wsUrl(origin) {
  return "wss://" + new URL(origin).host + "/api/ws/activity";
}

export function bySheetPath({ spreadsheetId, gid }) {
  let q = "spreadsheet=" + encodeURIComponent(spreadsheetId);
  if (gid !== null && gid !== undefined) q += "&gid=" + gid;
  return "/api/puzzles/by-sheet?" + q;
}

export function messagesPath(channelId) {
  return "/api/channels/" + encodeURIComponent(channelId) + "/messages?limit=50";
}

export function safeAvatarUrl(url) {
  try {
    return new URL(url).protocol === "https:" ? url : null;
  } catch {
    return null;
  }
}
