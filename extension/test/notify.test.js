import { describe, it, expect } from "vitest";
import { chatNotificationId, notificationId, notifyKind, notifyPrefs, parseNotificationId, solvedChannel } from "../src/lib/notify.js";

describe("notification ids", () => {
  it("round-trips tab and window", () => {
    expect(parseNotificationId(notificationId(12, 345))).toEqual({ tabId: 12, windowId: 345 });
    expect(parseNotificationId(notificationId(7, 9))).toEqual({ tabId: 7, windowId: 9 });
  });
  it("ids are unique per call", () => {
    expect(notificationId(1, 2, 100)).not.toBe(notificationId(1, 2, 101));
  });
  it("malformed ids give null", () => {
    for (const bad of ["", "foo", "tab:1", "tab:a:2:3", "tab:1:b:3", "xtab:1:2:3", "tab:1:2", "tab:1:2:3:4"]) {
      expect(parseNotificationId(bad)).toBeNull();
    }
    expect(parseNotificationId(undefined)).toBeNull();
  });
  it("a chat id round-trips hunt, channel and window", () => {
    expect(parseNotificationId(chatNotificationId("G0RVDF", "900", 345))).toEqual({ huntUid: "G0RVDF", channelId: "900", windowId: 345 });
    expect(parseNotificationId(chatNotificationId("H1", "901", 9))).toEqual({ huntUid: "H1", channelId: "901", windowId: 9 });
  });
  it("a hunt uid with a colon survives", () => {
    expect(parseNotificationId(chatNotificationId("a:b", "900", 1))).toEqual({ huntUid: "a:b", channelId: "900", windowId: 1 });
  });
  it("malformed chat ids give null", () => {
    for (const bad of ["chat:H1", "chat:H1:x:1:2", "chat:H1:900:y:2", "chat::900:1:2", "chat:%:900:1:2"]) {
      expect(parseNotificationId(bad)).toBeNull();
    }
  });
});

describe("solvedChannel", () => {
  const bot = (content, embeds = []) => ({ kind: 1, content, embeds });
  it("a solved embed is about the channel it is posted in", () => {
    expect(solvedChannel(bot("", [{ title: "PUZZLE SOLVED!" }]), "900")).toBe("900");
    expect(solvedChannel(bot("", [{ title: "Partially SOLVED!" }]), "901")).toBe("901");
  });
  it("a feeder line in a meta is about the feeder it links", () => {
    expect(solvedChannel(bot("✅ <#555> solved: `X`"), "900")).toBe("555");
    expect(solvedChannel(bot("✅ <#556> solved: `Y`"), "900")).toBe("556");
  });
  it("a feeder with no channel (bold name) is about nothing we can link", () => {
    expect(solvedChannel(bot("✅ **Mini** solved: `X`"), "900")).toBeNull();
  });
  it("anything that is not a solve is null", () => {
    expect(solvedChannel({ kind: 0, content: "✅ <#555> solved: `X`" }, "900")).toBeNull();
    expect(solvedChannel(bot("↩️ <#555> unsolved"), "900")).toBeNull();
  });
});

describe("notifyKind", () => {
  const bot = (content, embeds = []) => ({ kind: 1, content, embeds });
  it("people are a message; commands and other bots are nothing", () => {
    expect(notifyKind({ kind: 0, content: "hi" })).toBe("message");
    expect(notifyKind({ kind: 3, content: "!solve x" })).toBeNull();
    expect(notifyKind({ kind: 2, content: "hi", embeds: [{ title: "PUZZLE SOLVED!" }] })).toBeNull();
  });
  it("this bot: solve announcements are a solve, anything else nothing", () => {
    expect(notifyKind(bot("✅ <#1> solved: `X`"))).toBe("solve");
    expect(notifyKind(bot("↩️ <#1> unsolved"))).toBeNull();
    expect(notifyKind(bot("", [{ title: "PUZZLE SOLVED!" }]))).toBe("solve");
    expect(notifyKind(bot("", [{ title: "Partially SOLVED!" }]))).toBe("solve");
    expect(notifyKind(bot("", [{ title: "PUZZLE SOLUTION UPDATED!" }]))).toBeNull();
  });
  it("a person typing a solve-shaped message is still a message", () => {
    expect(notifyKind({ kind: 0, content: "✅ <#1> solved: `X`" })).toBe("message");
  });
  it("missing kind or message is nothing", () => {
    expect(notifyKind({ content: "hi" })).toBeNull();
    expect(notifyKind(undefined)).toBeNull();
  });
});

describe("notifyPrefs", () => {
  it("reads the two switches", () => {
    expect(notifyPrefs({ notifyMessages: true, notifySolves: false })).toEqual({ messages: true, solves: false });
    expect(notifyPrefs({ notifyMessages: false, notifySolves: true })).toEqual({ messages: false, solves: true });
  });
  it("an old single switch carries over to both", () => {
    expect(notifyPrefs({ notify: true })).toEqual({ messages: true, solves: true });
    expect(notifyPrefs({ notify: false })).toEqual({ messages: false, solves: false });
  });
  it("a new switch, once set, wins over the old one", () => {
    expect(notifyPrefs({ notify: true, notifyMessages: false })).toEqual({ messages: false, solves: true });
  });
  it("nothing stored is off", () => {
    expect(notifyPrefs({})).toEqual({ messages: false, solves: false });
    expect(notifyPrefs(undefined)).toEqual({ messages: false, solves: false });
  });
});
