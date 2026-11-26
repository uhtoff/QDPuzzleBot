// A note keeps a Discord link as the full URL (Discord needs that to show it as a
// link), but on the dashboard a bare https://discord.com/channels/<server>/<channel>/<message>
// reads better as the puzzle whose channel it points into.
import { describe, expect, it } from "vitest";
import { channelNamesFromBoard, discordLinkLabel, parseDiscordLink } from "./discordLinks";
import type { BoardGroup } from "../types";

describe("parseDiscordLink", () => {
  it("a message link names its channel and message", () => {
    expect(parseDiscordLink("https://discord.com/channels/1227/1574/1575")).toEqual({ channelId: "1574", messageId: "1575" });
  });
  it("a channel link has no message", () => {
    expect(parseDiscordLink("https://discord.com/channels/1227/1574")).toEqual({ channelId: "1574", messageId: null });
  });
  it("other Discord hosts and DMs count", () => {
    expect(parseDiscordLink("https://ptb.discord.com/channels/1/2/3")).toEqual({ channelId: "2", messageId: "3" });
    expect(parseDiscordLink("https://discordapp.com/channels/@me/2/3")).toEqual({ channelId: "2", messageId: "3" });
  });
  it("anything else is not a Discord link", () => {
    for (const url of [
      "https://ex.com/channels/1/2/3",
      "https://discord.com/invite/abc",
      "https://discord.com.evil.example/channels/1/2/3",
      "https://discord.com/channels/1",
      "not a url",
    ]) {
      expect(parseDiscordLink(url)).toBeNull();
    }
  });
});

describe("discordLinkLabel", () => {
  const names = new Map([["1574", "Cap ONe"]]);
  const name = (id: string) => names.get(id) ?? null;
  it("a known channel shows its puzzle", () => {
    expect(discordLinkLabel({ channelId: "1574", messageId: "1575" }, name)).toBe("#Cap ONe › 💬");
    expect(discordLinkLabel({ channelId: "1574", messageId: null }, name)).toBe("#Cap ONe");
  });
  it("an unknown channel says what kind of link it is", () => {
    expect(discordLinkLabel({ channelId: "9", messageId: "1" }, name)).toBe("Discord message");
    expect(discordLinkLabel({ channelId: "9", messageId: null }, name)).toBe("Discord channel");
  });
});

describe("channelNamesFromBoard", () => {
  it("maps every puzzle's and mini's channel to its name, and the hunt's general channel", () => {
    const board = [
      { puzzles: [{ id: 1, name: "Alpha", channel_id: "11", mini_puzzles: [{ id: 3, name: "Mini", channel_id: "33" }] }] },
      { puzzles: [{ id: 2, name: "Beta", channel_id: "22" }, { id: 4, name: "No channel", channel_id: null }] },
    ] as unknown as BoardGroup[];
    const names = channelNamesFromBoard(board, { name: "Singletons", channel_id: "99" });
    expect(names.get("11")).toBe("Alpha");
    expect(names.get("22")).toBe("Beta");
    expect(names.get("33")).toBe("Mini");
    expect(names.get("99")).toBe("Singletons (general)");
    expect(names.size).toBe(4);
  });
});
