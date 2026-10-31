import { emojify } from "../emoji";

// The bot's command replies are written for Discord: `:shortcode:` emoji,
// **bold** and `code`. Pages that show those replies verbatim (the setup
// result) split them into spans here and render each span, so they read as
// they do in Discord instead of showing the raw markup.
export type DiscordSpan = { kind: "text" | "bold" | "code"; text: string };

const TOKEN_RE = /(\*\*[^*]+\*\*|`[^`]+`)/g;

/** Split a Discord-formatted line into text/bold/code spans. Emoji shortcodes
 *  become unicode in text and bold spans; code spans are left as written. */
export function discordSpans(line: string): DiscordSpan[] {
  const spans: DiscordSpan[] = [];
  for (const part of line.split(TOKEN_RE)) {
    if (!part) continue;
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      spans.push({ kind: "bold", text: emojify(part.slice(2, -2)) });
    } else if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
      spans.push({ kind: "code", text: part.slice(1, -1) });
    } else {
      spans.push({ kind: "text", text: emojify(part) });
    }
  }
  return spans;
}
