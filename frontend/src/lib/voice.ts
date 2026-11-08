import type { ActivityEvent } from "../types";

/** Live voice head-counts from `voice` websocket events. Puzzle counts are keyed
 *  by the puzzle's TEXT channel id (the event's channel_id); the hunt's general
 *  voice count is keyed by hunt_uid. */
export interface LiveVoice {
  channels: Record<string, number>;
  hunts: Record<string, number>;
}

export const emptyVoice: LiveVoice = { channels: {}, hunts: {} };

export function applyVoiceEvent(state: LiveVoice, ev: ActivityEvent): LiveVoice {
  if (ev.type !== "voice" || !ev.channel_id || typeof ev.voice_count !== "number") return state;
  const next: LiveVoice = {
    channels: { ...state.channels, [ev.channel_id]: ev.voice_count },
    hunts: state.hunts,
  };
  if (ev.hunt_uid) next.hunts = { ...state.hunts, [ev.hunt_uid]: ev.voice_count };
  return next;
}

/** A live event overrides the payload's count once one has arrived. */
export function puzzleVoiceCount(
  p: { channel_id: string | null; voice_count: number },
  live: LiveVoice,
): number {
  const hit = p.channel_id ? live.channels[p.channel_id] : undefined;
  return hit ?? p.voice_count ?? 0;
}

export function huntVoiceCount(uid: string, payloadCount: number, live: LiveVoice): number {
  return live.hunts[uid] ?? payloadCount ?? 0;
}

/** Row background: solved green wins; otherwise teal when someone is in voice. */
export function voiceRowBg(solved: boolean, count: number): string | undefined {
  if (solved) return "var(--mantine-color-green-light)";
  return count > 0 ? "var(--mantine-color-teal-light)" : undefined;
}
