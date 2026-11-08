import { describe, expect, it } from "vitest";
import { applyVoiceEvent, emptyVoice, huntVoiceCount, MOVE_MEMBERS_WARNING, puzzleVoiceCount, voiceCreateNotices, voiceRowBg } from "./voice";

describe("applyVoiceEvent", () => {
  it("keys a puzzle event by text channel id and ignores other event types", () => {
    let s = applyVoiceEvent(emptyVoice, { type: "voice", channel_id: "111", voice_count: 2 });
    s = applyVoiceEvent(s, { type: "voice", channel_id: "222", voice_count: 1 });
    expect(s.channels).toEqual({ "111": 2, "222": 1 });
    expect(applyVoiceEvent(s, { type: "activity", channel_id: "111", voice_count: 9 })).toBe(s);
  });

  it("keys a hunt-general event by hunt_uid as well", () => {
    const s = applyVoiceEvent(emptyVoice, { type: "voice", channel_id: "5", hunt_uid: "h1", voice_count: 3 });
    expect(s.hunts).toEqual({ h1: 3 });
  });

  it("a later event overrides an earlier count, including back to 0", () => {
    let s = applyVoiceEvent(emptyVoice, { type: "voice", channel_id: "111", voice_count: 2 });
    s = applyVoiceEvent(s, { type: "voice", channel_id: "111", voice_count: 0 });
    expect(s.channels["111"]).toBe(0);
  });
});

describe("counts", () => {
  const p = { channel_id: "111", voice_count: 1 };
  it("falls back to the payload count until an event arrives, then the event wins", () => {
    expect(puzzleVoiceCount(p, emptyVoice)).toBe(1);
    const s = applyVoiceEvent(emptyVoice, { type: "voice", channel_id: "111", voice_count: 0 });
    expect(puzzleVoiceCount(p, s)).toBe(0);
    expect(puzzleVoiceCount({ channel_id: null, voice_count: 4 }, s)).toBe(4);
  });
  it("hunt count follows hunt_uid", () => {
    const s = applyVoiceEvent(emptyVoice, { type: "voice", channel_id: "5", hunt_uid: "h1", voice_count: 3 });
    expect(huntVoiceCount("h1", 0, s)).toBe(3);
    expect(huntVoiceCount("h2", 7, s)).toBe(7);
  });
});

describe("voiceRowBg", () => {
  it("tints indigo only for an unsolved puzzle with someone in voice", () => {
    expect(voiceRowBg(false, 2)).toBe("var(--mantine-color-indigo-light)");
    expect(voiceRowBg(false, 0)).toBeUndefined();
    expect(voiceRowBg(true, 2)).toBe("var(--mantine-color-green-light)");
    expect(voiceRowBg(true, 0)).toBe("var(--mantine-color-green-light)");
  });
});

describe("voiceCreateNotices", () => {
  it("confirms a new channel in green", () => {
    expect(voiceCreateNotices({ created: true, move_members: true })).toEqual([
      { message: "Voice channel created.", color: "green" },
    ]);
  });
  it("says when the channel already existed", () => {
    expect(voiceCreateNotices({ created: false, move_members: true })).toEqual([
      { message: "This puzzle already has a voice channel.", color: "green" },
    ]);
  });
  it("adds a yellow warning when the bot lacks Move Members", () => {
    expect(voiceCreateNotices({ created: true, move_members: false })).toEqual([
      { message: "Voice channel created.", color: "green" },
      { message: MOVE_MEMBERS_WARNING, color: "yellow" },
    ]);
    expect(MOVE_MEMBERS_WARNING).toBe(
      "I don't have Move Members, so people can't be moved out when this channel closes — they'll be disconnected instead.",
    );
  });
});
