import { describe, it, expect } from "vitest";
import { notificationId, parseNotificationId } from "../src/lib/notify.js";

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
});

import { shouldNotify } from "../src/lib/notify.js";

describe("shouldNotify", () => {
  const bot = (content, embeds = []) => ({ kind: 1, content, embeds });
  it("people yes, commands and other bots no", () => {
    expect(shouldNotify({ kind: 0, content: "hi" })).toBe(true);
    expect(shouldNotify({ kind: 3, content: "!solve x" })).toBe(false);
    expect(shouldNotify({ kind: 2, content: "hi", embeds: [{ title: "PUZZLE SOLVED!" }] })).toBe(false);
  });
  it("this bot: solve announcements only", () => {
    expect(shouldNotify(bot("✅ <#1> solved: `X`"))).toBe(true);
    expect(shouldNotify(bot("↩️ <#1> unsolved"))).toBe(false);
    expect(shouldNotify(bot("", [{ title: "PUZZLE SOLVED!" }]))).toBe(true);
    expect(shouldNotify(bot("", [{ title: "Partially SOLVED!" }]))).toBe(true);
    expect(shouldNotify(bot("", [{ title: "PUZZLE SOLUTION UPDATED!" }]))).toBe(false);
  });
  it("missing kind or message is false", () => {
    expect(shouldNotify({ content: "hi" })).toBe(false);
    expect(shouldNotify(undefined)).toBe(false);
  });
});
