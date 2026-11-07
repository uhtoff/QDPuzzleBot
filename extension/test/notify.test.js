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
