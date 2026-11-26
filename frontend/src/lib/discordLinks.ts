import type { BoardGroup } from "../types";

// Discord channel/message links in notes. The note keeps the full URL (Discord
// shows it as a "#channel › 💬" chip); the dashboard shows the same chip, naming
// the puzzle whose channel the link points into.

export interface DiscordLink {
  channelId: string;
  messageId: string | null;
}

// discord.com, its ptb./canary. builds, and the old discordapp.com. The server
// part is a snowflake, or @me for DMs.
const LINK = /^https:\/\/(?:(?:ptb|canary)\.)?discord(?:app)?\.com\/channels\/(?:\d+|@me)\/(\d+)(?:\/(\d+))?\/?$/;

export function parseDiscordLink(url: string): DiscordLink | null {
  const m = LINK.exec(url);
  return m ? { channelId: m[1], messageId: m[2] ?? null } : null;
}

/** "#Puzzle name › 💬" for a message, "#Puzzle name" for a channel; a channel the
 *  dashboard can't name says which kind of link it is. */
export function discordLinkLabel(link: DiscordLink, channelName: (id: string) => string | null): string {
  const name = channelName(link.channelId);
  if (!name) return link.messageId ? "Discord message" : "Discord channel";
  return link.messageId ? `#${name} › 💬` : `#${name}`;
}

/** Channel id -> name for every puzzle and mini on the board, plus the hunt's
 *  general channel (named as the chat extension names it). */
export function channelNamesFromBoard(
  board: BoardGroup[],
  hunt?: { name: string; channel_id?: string | null },
): Map<string, string> {
  const names = new Map<string, string>();
  for (const g of board) {
    for (const p of g.puzzles) {
      if (p.channel_id) names.set(p.channel_id, p.name);
      for (const m of p.mini_puzzles ?? []) if (m.channel_id) names.set(m.channel_id, m.name);
    }
  }
  if (hunt?.channel_id) names.set(hunt.channel_id, `${hunt.name} (general)`);
  return names;
}
