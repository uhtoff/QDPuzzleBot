import { describe, it, expect } from "vitest";
import { parseHuntBoardUrl, huntChatPath } from "../src/lib/origin.js";
import { huntCacheValid, stateFor } from "../src/lib/panelState.js";

const O = "https://quarantinedecrypters.com";

describe("parseHuntBoardUrl", () => {
  it("returns the uid of a hunt board on the configured origin", () => {
    expect(parseHuntBoardUrl(O + "/hunts/abc123", O)).toBe("abc123");
    expect(parseHuntBoardUrl(O + "/hunts/abc123/", O)).toBe("abc123");
    expect(parseHuntBoardUrl(O + "/hunts/abc123?view=all#x", O)).toBe("abc123");
  });

  it("ignores other dashboard pages", () => {
    expect(parseHuntBoardUrl(O + "/", O)).toBeNull();
    expect(parseHuntBoardUrl(O + "/hunts", O)).toBeNull();
    expect(parseHuntBoardUrl(O + "/guilds/5/settings", O)).toBeNull();
    expect(parseHuntBoardUrl(O + "/hunts/abc/extra", O)).toBeNull();
  });

  it("ignores other origins, junk and a missing origin", () => {
    expect(parseHuntBoardUrl("https://puzzbot.com/hunts/abc123", O)).toBeNull();
    expect(parseHuntBoardUrl("https://evil.example/hunts/abc123", O)).toBeNull();
    expect(parseHuntBoardUrl("not a url", O)).toBeNull();
    expect(parseHuntBoardUrl(O + "/hunts/abc123", undefined)).toBeNull();
    expect(parseHuntBoardUrl(O + "/hunts/%E0%A4%A", O)).toBeNull(); // bad escape
  });

  it("builds the chat lookup path", () => {
    expect(huntChatPath("abc 1")).toBe("/api/hunts/abc%201/chat");
  });
});

describe("huntCacheValid", () => {
  const entry = { hunt_chat: true, hunt_uid: "abc", channel_id: "9", bridge_enabled: true };

  it("matches only a hunt-chat entry for the same hunt", () => {
    expect(huntCacheValid(entry, "abc")).toBe(true);
    expect(huntCacheValid(entry, "def")).toBe(false);
    expect(huntCacheValid({ ...entry, hunt_chat: undefined }, "abc")).toBe(false); // a puzzle in that hunt
    expect(huntCacheValid(undefined, "abc")).toBe(false);
  });

  it("a hunt-chat entry is a normal chat state", () => {
    expect(stateFor(entry)).toBe("chat");
  });
});
