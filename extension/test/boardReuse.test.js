import { describe, it, expect } from "vitest";
import { boardReuseTarget, createArrivals, handOverBoardLink } from "../src/lib/boardReuse.js";

const origin = "https://dash.test";
const b = (uid, q = "") => `https://dash.test/hunts/${uid}${q}`;
const decide = (newTab, tabs) => boardReuseTarget({ newTab, tabs, origin });

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

  it("ignores a background tab opened from a dashboard tab (middle-click, Ctrl+click)", () => {
    const fromDash = [
      { id: 1, url: b("h1"), windowId: 9 },
      { id: 2, url: b("h1", "?image=5"), windowId: 9, openerTabId: 1, active: false },
    ];
    expect(decide(fromDash[1], fromDash)).toBeNull();
    const fromDashOtherPage = [
      { id: 1, url: b("h1"), windowId: 9 },
      { id: 3, url: "https://dash.test/", windowId: 9 },
      { id: 2, url: b("h1"), windowId: 9, openerTabId: 3, active: false },
    ];
    expect(decide(fromDashOtherPage[2], fromDashOtherPage)).toBeNull();
    const fromDiscord = [
      { id: 1, url: b("h1"), windowId: 9 },
      { id: 3, url: "https://discord.com/channels/1/2", windowId: 9 },
      { id: 2, url: b("h1"), windowId: 9, openerTabId: 3, active: false },
    ];
    expect(decide(fromDiscord[2], fromDiscord)).toBe(1);
  });

  it("folds a foreground tab whose opener is a dashboard tab (Chrome names the active tab for a link from another app)", () => {
    const tabs = [
      { id: 1, url: b("h1"), windowId: 9 },
      { id: 2, url: b("h1", "?image=5"), windowId: 9, openerTabId: 1, active: true },
    ];
    expect(decide(tabs[1], tabs)).toBe(1);
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
  const fakeApi = (sendMessage, currentUrl = async () => url) => {
    const calls = [];
    const api = {
      // Not recorded in calls: a read, and the sequences below are about what changes.
      currentUrl: (id) => currentUrl(id),
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

  it("leaves the new tab, and focus, alone when it has moved on during the handoff", async () => {
    const { api, calls } = fakeApi(async () => true, async () => "https://elsewhere.example/");
    expect(await handOverBoardLink({ targetId: 1, newTabId: 2, url, api })).toBe("handed");
    expect(calls).toEqual([["sendMessage", 1, { type: "openBoardLink", search: "?puzzle=3&image=50" }]]);
  });

  it("leaves things alone after a reload fallback too, if the new tab moved on", async () => {
    const { api, calls } = fakeApi(async () => false, async () => b("h1", "?puzzle=9"));
    expect(await handOverBoardLink({ targetId: 1, newTabId: 2, url, api })).toBe("reloaded");
    expect(calls.slice(1)).toEqual([["update", 1, { url }]]);
  });

  it("does nothing more when the new tab is already gone", async () => {
    const { api, calls } = fakeApi(async () => true, async () => {
      throw new Error("No tab with id: 2");
    });
    expect(await handOverBoardLink({ targetId: 1, newTabId: 2, url, api })).toBe("handed");
    expect(calls.map((c) => c[0])).toEqual(["sendMessage"]);
  });

  it("checks the new tab where it is when the handoff ends, not when it began", async () => {
    let now = url;
    const { api, calls } = fakeApi(async () => {
      now = "https://elsewhere.example/"; // navigated while the board was being asked
      return true;
    }, async () => now);
    await handOverBoardLink({ targetId: 1, newTabId: 2, url, api });
    expect(calls.map((c) => c[0])).toEqual(["sendMessage"]);
  });

  it("swallows focus and remove errors", async () => {
    const { api } = fakeApi(async () => true);
    api.focus = async () => { throw new Error("gone"); };
    api.remove = async () => { throw new Error("gone"); };
    expect(await handOverBoardLink({ targetId: 1, newTabId: 2, url, api })).toBe("handed");
  });
});

describe("createArrivals (only a new tab's first page counts)", () => {
  it("counts a new tab's first page", () => {
    const a = createArrivals();
    a.created(2, 0);
    expect(a.arrived(2, b("h1", "?image=5"), 10)).toBe(true);
  });

  it("does not count a later URL change in that tab (reload, the SPA's own query change)", () => {
    const a = createArrivals();
    a.created(2, 0);
    a.arrived(2, b("h1", "?image=5"), 10);
    expect(a.arrived(2, b("h1"), 20)).toBe(false);
  });

  it("does not count a tab it never saw created (open before the update, or same-tab navigation)", () => {
    const a = createArrivals();
    expect(a.arrived(1, b("h1", "?puzzle=3"), 10)).toBe(false);
  });

  it("does not count a board reached after the tab's first page (hunt list → board)", () => {
    const a = createArrivals();
    a.created(2, 0);
    a.arrived(2, "https://dash.test/hunts", 10); // the first page: not a board, so nothing is folded
    expect(a.arrived(2, b("h1"), 20)).toBe(false);
  });

  it("waits past a blank or browser page for the first real one", () => {
    const a = createArrivals();
    a.created(2, 0);
    expect(a.arrived(2, "about:blank", 5)).toBe(false);
    expect(a.arrived(2, "chrome://newtab/", 6)).toBe(false);
    expect(a.arrived(2, b("h1"), 10)).toBe(true);
  });

  it("forgets a new tab after the window", () => {
    const a = createArrivals(30000);
    a.created(2, 0);
    expect(a.arrived(2, b("h1"), 30001)).toBe(false);
  });

  it("forgets a closed tab", () => {
    const a = createArrivals();
    a.created(2, 0);
    a.removed(2);
    expect(a.arrived(2, b("h1"), 10)).toBe(false);
  });

  it("ignores tabs the browser restores at startup", () => {
    const a = createArrivals(30000);
    a.quietUntil(15000);
    a.created(2, 1000);
    expect(a.arrived(2, b("h1"), 2000)).toBe(false);
    a.created(3, 16000);
    expect(a.arrived(3, b("h1"), 16500)).toBe(true);
  });
});
