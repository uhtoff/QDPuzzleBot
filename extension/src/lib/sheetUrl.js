// Parse a Google Sheets URL into {spreadsheetId, gid}; anything else -> null.
export function parseSheetUrl(url) {
  let u;
  try {
    u = new URL(url);
  } catch {
    return null;
  }
  if (u.protocol !== "https:" || u.hostname !== "docs.google.com") return null;
  const m = u.pathname.match(/^\/spreadsheets\/d\/([^/]+)/);
  if (!m) return null;
  const hashGid = u.hash.match(/(?:^#|&)gid=(\d+)/);
  const gidStr = hashGid ? hashGid[1] : u.searchParams.get("gid");
  const gid = gidStr !== null && /^\d+$/.test(gidStr) ? Number(gidStr) : null;
  return { spreadsheetId: m[1], gid };
}
