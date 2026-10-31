// Discord renders `:shortcode:` emoji in message text the user *types* (converted
// to unicode before send), but the bot's own embed/message strings carry literal
// shortcodes (`:partying_face:`, `:x:`, …) that arrive verbatim. The chat viewer
// renders text as plain strings, so those show literally. Map the shortcodes the
// bot actually emits (plus common ones) back to unicode at render time. Unknown
// shortcodes are left untouched.
const SHORTCODES: Record<string, string> = {
  // Emitted by the bot (solve/partial/unsolve/welcome embeds + command replies)
  x: "❌",
  white_check_mark: "✅",
  warning: "⚠️",
  exclamation: "❗",
  partying_face: "🥳",
  information_source: "ℹ️",
  hourglass: "⌛",
  hourglass_flowing_sand: "⏳",
  grey_question: "❔",
  wastebasket: "🗑️",
  pencil: "📝",
  no_entry_sign: "🚫",
  dog: "🐶",
  mag_right: "🔍",
  mag: "🔍",
  broom: "🧹",
  biohazard: "☣️",
  incoming_envelope: "📨",
  door: "🚪",
  wave: "👋",
  // Common extras likely to appear from the bot or other integrations
  tada: "🎉",
  heavy_check_mark: "✔️",
  question: "❓",
  bulb: "💡",
  fire: "🔥",
  sparkles: "✨",
  thumbsup: "👍",
  "+1": "👍",
  thumbsdown: "👎",
  "-1": "👎",
  rocket: "🚀",
  eyes: "👀",
  lock: "🔒",
  unlock: "🔓",
  star: "⭐",
  checkered_flag: "🏁",
  trophy: "🏆",
  "100": "💯",
  point_right: "👉",
  arrow_right: "➡️",
  clipboard: "📋",
  link: "🔗",
};

const SHORTCODE_RE = /:([a-z0-9_+-]+):/g;

/** Replace known `:shortcode:` tokens with their unicode emoji; leave the rest. */
export function emojify(text: string | null | undefined): string {
  if (!text) return text ?? "";
  return text.replace(SHORTCODE_RE, (whole, code: string) =>
    Object.prototype.hasOwnProperty.call(SHORTCODES, code) ? SHORTCODES[code] : whole);
}
