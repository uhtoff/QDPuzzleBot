export const DEFAULT_ORIGIN = "https://quarantinedecrypters.com";

// The Google Sheets host permission (manifest host_permissions). Chrome always grants it;
// Firefox grants it at install but lets the user revoke it in about:addons.
export const SHEETS_ORIGINS = ["https://docs.google.com/spreadsheets/*"];

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

export function wsUrl(origin, ticket) {
  const base = "wss://" + new URL(origin).host + "/api/ws/activity";
  return ticket ? base + "?ticket=" + encodeURIComponent(ticket) : base;
}

export const WS_TICKET_PATH = "/api/extension/ws-ticket";

export function bySheetPath({ spreadsheetId, gid }) {
  let q = "spreadsheet=" + encodeURIComponent(spreadsheetId);
  if (gid !== null && gid !== undefined) q += "&gid=" + gid;
  return "/api/puzzles/by-sheet?" + q;
}

// The hunt uid when `url` is a hunt board on the dashboard (`<origin>/hunts/<uid>`,
// any query), else null. Other dashboard pages (hunt list, settings) are not hunts.
export function parseHuntBoardUrl(url, origin) {
  let u;
  try {
    u = new URL(url);
  } catch {
    return null;
  }
  if (!origin || u.origin !== origin) return null;
  const m = /^\/hunts\/([^/]+)\/?$/.exec(u.pathname);
  if (!m) return null;
  try {
    return decodeURIComponent(m[1]);
  } catch {
    return null;
  }
}

// The puzzle whose drawer is open on a hunt board (`?puzzle=<id>`, which the dashboard
// keeps in the address while the drawer is open), else null.
export function parseBoardPuzzleId(url, origin) {
  if (!parseHuntBoardUrl(url, origin)) return null;
  const raw = new URL(url).searchParams.get("puzzle");
  return raw && /^\d+$/.test(raw) ? Number(raw) : null;
}

// A puzzle's chat by id (the drawer's puzzle), in the by-sheet lookup's shape.
export function puzzleChatPath(id) {
  return "/api/puzzles/" + encodeURIComponent(id) + "/chat";
}

// The hunt's general channel, in the by-sheet lookup's shape.
export function huntChatPath(uid) {
  return "/api/hunts/" + encodeURIComponent(uid) + "/chat";
}

// The unread summary for the extension's unread list (POST, with read markers).
export function huntUnreadPath(uid) {
  return "/api/hunts/" + encodeURIComponent(uid) + "/unread";
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

// Did a permissions.onRemoved `removed.origins` list take away `origin`'s access?
export function removesOrigin(removedOrigins, origin) {
  return (removedOrigins ?? []).some((o) => {
    try {
      return new URL(String(o).replace(/^\*:/, "https:")).origin === origin;
    } catch {
      return false;
    }
  });
}
