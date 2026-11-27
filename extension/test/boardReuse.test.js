import { describe, it, expect } from "vitest";
import { boardReuseTarget } from "../src/lib/boardReuse.js";

const origin = "https://dash.test";
const b = (uid, q = "") => `https://dash.test/hunts/${uid}${q}`;
const decide = (newTab, tabs, beforeUid) => boardReuseTarget({ newTab, beforeUid, tabs, origin });

describe("boardReuseTarget", () => {
  it("folds a new board tab into an open board of the same hunt", () => {
    const tabs = [
      { id: 1, url: b("h1"), windowId: 9, lastAccessed: 5 },
      { id: 2, url: b("h1", "?image=50"), windowId: 9 },
    ];
    expect(decide(tabs[1], tabs)).toBe(1);
  });

  it("ignores a different hunt", () => {
    const tabs = [
      { id: 1, url: b("h2"), windowId: 9 },
      { id: 2, url: b("h1"), windowId: 9 },
    ];
    expect(decide(tabs[1], tabs)).toBeNull();
  });

  it("ignores the same hunt as before", () => {
    const tabs = [
      { id: 1, url: b("h1"), windowId: 9 },
      { id: 2, url: b("h1", "?puzzle=3"), windowId: 9 },
    ];
    expect(decide(tabs[1], tabs, "h1")).toBeNull();
  });

  it("ignores a tab opened from a dashboard tab", () => {
    const fromDash = [
      { id: 1, url: b("h1"), windowId: 9 },
      { id: 2, url: b("h1", "?image=5"), windowId: 9, openerTabId: 1 },
    ];
    expect(decide(fromDash[1], fromDash)).toBeNull();
    const fromDashOtherPage = [
      { id: 1, url: b("h1"), windowId: 9 },
      { id: 3, url: "https://dash.test/", windowId: 9 },
      { id: 2, url: b("h1"), windowId: 9, openerTabId: 3 },
    ];
    expect(decide(fromDashOtherPage[2], fromDashOtherPage)).toBeNull();
    const fromDiscord = [
      { id: 1, url: b("h1"), windowId: 9 },
      { id: 3, url: "https://discord.com/channels/1/2", windowId: 9 },
      { id: 2, url: b("h1"), windowId: 9, openerTabId: 3 },
    ];
    expect(decide(fromDiscord[2], fromDiscord)).toBe(1);
  });

  it("ignores the only tab in its window", () => {
    const tabs = [
      { id: 1, url: b("h1"), windowId: 9 },
      { id: 2, url: b("h1"), windowId: 8 },
    ];
    expect(decide(tabs[1], tabs)).toBeNull();
  });

  it("ignores non-board and other-origin URLs", () => {
    const other = [
      { id: 1, url: b("h1"), windowId: 9 },
      { id: 2, url: "https://other.test/hunts/h1", windowId: 9 },
    ];
    expect(decide(other[1], other)).toBeNull();
    const settings = [
      { id: 1, url: b("h1"), windowId: 9 },
      { id: 2, url: "https://dash.test/hunts/h1/settings", windowId: 9 },
    ];
    expect(decide(settings[1], settings)).toBeNull();
  });

  it("prefers the new tab's window, then the most recently used", () => {
    const tabs = [
      { id: 1, url: b("h1"), windowId: 7, lastAccessed: 100 },
      { id: 2, url: b("h1"), windowId: 9, lastAccessed: 1 },
      { id: 3, url: b("h1", "?image=1"), windowId: 9 },
    ];
    expect(decide(tabs[2], tabs)).toBe(2);
    const elsewhere = [
      { id: 2, url: b("h1"), windowId: 9, lastAccessed: 1 },
      { id: 1, url: b("h1"), windowId: 7, lastAccessed: 100 },
      { id: 4, url: "https://discord.com/app", windowId: 5 },
      { id: 3, url: b("h1", "?image=1"), windowId: 5 },
    ];
    expect(decide(elsewhere[3], elsewhere)).toBe(1);
  });
});
