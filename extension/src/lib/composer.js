import { isBangCommand } from "./format.js";

export const MAX_LEN = 2000;

// Can the panel offer a composer for this state/puzzle?
export function canCompose(state, puzzle) {
  return state === "chat" && !!puzzle && !!puzzle.channel_id && !puzzle.hunt_archived;
}

// Client-side pre-send check. Returns {ok: true, content} or {ok: false, code}
// where code is an errorText() key. The server still enforces all of this.
export function validate(text) {
  const content = text.trim();
  if (!content) return { ok: false, code: "empty" };
  if (content.length > MAX_LEN) return { ok: false, code: "too_long" };
  if (isBangCommand(content)) return { ok: false, code: "command" };
  return { ok: true, content };
}
