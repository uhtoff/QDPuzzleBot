import { parseSheetUrl } from "./sheetUrl.js";

export const MAX_URLS = 100;
export const MAX_NAME = 100;
const MAX_URL_LEN = 2048;

const isSheetUrl = (u) => typeof u === "string" && u.length <= MAX_URL_LEN && parseSheetUrl(u) !== null;

// R14 gate + payload validation for messages from the dashboard content script.
// Returns {ok: true, action, payload} with a rebuilt (whitelisted) payload, or {ok: false}.
export function validateAction(msg, { senderId, senderUrl, senderHasTab, runtimeId, origin }) {
  const no = { ok: false };
  if (senderId !== runtimeId || !senderHasTab) return no;
  let senderOrigin;
  try {
    senderOrigin = new URL(senderUrl ?? "").origin;
  } catch {
    return no;
  }
  if (senderOrigin !== origin) return no;
  if (!msg || typeof msg !== "object") return no;
  const p = msg.payload && typeof msg.payload === "object" ? msg.payload : {};
  switch (msg.action) {
    case "work":
      return isSheetUrl(p.sheetUrl) ? { ok: true, action: "work", payload: { sheetUrl: p.sheetUrl } } : no;
    case "openRound":
      if (
        typeof p.name === "string" &&
        p.name.trim() !== "" &&
        p.name.length <= MAX_NAME &&
        Array.isArray(p.sheetUrls) &&
        p.sheetUrls.length <= MAX_URLS &&
        p.sheetUrls.every(isSheetUrl)
      )
        return { ok: true, action: "openRound", payload: { name: p.name, sheetUrls: [...p.sheetUrls] } };
      return no;
    case "closeSolved":
      return { ok: true, action: "closeSolved", payload: {} };
    default:
      return no;
  }
}

// A tab has newly arrived at a spreadsheet (not a reload or in-file navigation).
export function isNewArrival(before, after) {
  return !!after && before !== after;
}
