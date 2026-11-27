import { describe, it, expect } from "vitest";
import { boardReuseTarget, handOverBoardLink } from "../src/lib/boardReuse.js";

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

describe("handOverBoardLink", () => {
  const fakeApi = (sendMessage) => {
    const calls = [];
    const api = {
      sendMessage: (...a) => (calls.push(["sendMessage", ...a]), sendMessage()),
      update: async (...a) => void calls.push(["update", ...a]),
      focus: async (...a) => void calls.push(["focus", ...a]),
      remove: async (...a) => void calls.push(["remove", ...a]),
    };
    return { api, calls };
  };
  const url = b("h1", "?puzzle=3&image=50");

  it("hands the search over and closes the new tab after", async () => {
    const { api, calls } = fakeApi(async () => true);
    expect(await handOverBoardLink({ targetId: 1, newTabId: 2, url, api })).toBe("handed");
    expect(calls).toEqual([
      ["sendMessage", 1, { type: "openBoardLink", search: "?puzzle=3&image=50" }],
      ["focus", 1],
      ["remove", 2],
    ]);
  });

  const expectReloaded = (calls) =>
    expect(calls.slice(1)).toEqual([["update", 1, { url }], ["focus", 1], ["remove", 2]]);

  it("falls back to navigating when the page declines", async () => {
    const { api, calls } = fakeApi(async () => false);
    expect(await handOverBoardLink({ targetId: 1, newTabId: 2, url, api })).toBe("reloaded");
    expectReloaded(calls);
  });

  it("falls back when sendMessage throws", async () => {
    const { api, calls } = fakeApi(async () => {
      throw new Error("Receiving end does not exist");
    });
    expect(await handOverBoardLink({ targetId: 1, newTabId: 2, url, api })).toBe("reloaded");
    expectReloaded(calls);
  });

  it("falls back when the page never answers", async () => {
    const { api, calls } = fakeApi(() => new Promise(() => {}));
    expect(await handOverBoardLink({ targetId: 1, newTabId: 2, url, api, timeoutMs: 10 })).toBe("reloaded");
    expectReloaded(calls);
  });

  it("a plain board link hands over an empty search", async () => {
    const { api, calls } = fakeApi(async () => true);
    await handOverBoardLink({ targetId: 1, newTabId: 2, url: b("h1"), api });
    expect(calls[0]).toEqual(["sendMessage", 1, { type: "openBoardLink", search: "" }]);
  });

  it("swallows focus and remove errors", async () => {
    const { api } = fakeApi(async () => true);
    api.focus = async () => { throw new Error("gone"); };
    api.remove = async () => { throw new Error("gone"); };
    expect(await handOverBoardLink({ targetId: 1, newTabId: 2, url, api })).toBe("handed");
  });
});
