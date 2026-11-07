import { describe, it, expect } from "vitest";
import { invalidateOnNavigate } from "../src/lib/tabRefresh.js";
import { createRegistry } from "../src/lib/registry.js";
import { createLookupGen } from "../src/lib/lookupGen.js";

const url = (id, gid = 0) => "https://docs.google.com/spreadsheets/d/" + id + "/edit#gid=" + gid;

function setup() {
  const posted = [];
  const registry = createRegistry();
  const lookupGen = createLookupGen();
  const failures = new Map();
  const failedFor = new Map();
  const port = {};
  const ports = new Map([[port, 7]]);
  const nav = (tab) => invalidateOnNavigate({ tab, registry, failures, failedFor, lookupGen, ports, post: (p, m) => posted.push(m) });
  // mirrors background.lookup's write-back rule
  const land = (tabId, token, sid) => {
    if (!lookupGen.isCurrent(tabId, token)) return false;
    registry.set(tabId, { spreadsheetId: sid, gid: 0, channel_id: "chanA" });
    return true;
  };
  return { posted, registry, lookupGen, failures, failedFor, nav, land };
}

describe("invalidateOnNavigate", () => {
  it("A->B: loading is delivered, A's late response cannot register A", () => {
    const t = setup();
    t.registry.set(1, { spreadsheetId: "A", gid: 0, channel_id: "chanA" });
    const tokenA = t.lookupGen.begin(1); // A's lookup in flight
    t.registry.delete(1); // (A's entry superseded earlier in flight)
    expect(t.nav({ id: 1, windowId: 7, active: true, url: url("B") })).toBe(true);
    expect(t.posted).toHaveLength(1);
    expect(t.posted[0].state).toBe("loading");
    expect(t.posted[0].puzzle).toBeNull();
    expect(t.land(1, tokenA, "A")).toBe(false);
    expect(t.registry.get(1)).toBeUndefined();
  });
  it("drops a registered A entry immediately", () => {
    const t = setup();
    t.registry.set(1, { spreadsheetId: "A", gid: 0, channel_id: "chanA" });
    t.nav({ id: 1, windowId: 7, active: true, url: url("B") });
    expect(t.registry.get(1)).toBeUndefined();
  });
  it("does not deliver to an inactive tab or other window, but still invalidates", () => {
    const t = setup();
    t.registry.set(1, { spreadsheetId: "A", gid: 0 });
    t.nav({ id: 1, windowId: 7, active: false, url: url("B") });
    expect(t.posted).toHaveLength(0);
    expect(t.registry.get(1)).toBeUndefined();
  });
  it("no invalidation or flash for the cached sheet, a non-sheet URL, or the same failing URL", () => {
    const t = setup();
    t.registry.set(1, { spreadsheetId: "A", gid: 0 });
    expect(t.nav({ id: 1, windowId: 7, active: true, url: url("A") })).toBe(false);
    expect(t.nav({ id: 1, windowId: 7, active: true, url: "https://example.com/" })).toBe(false);
    t.failedFor.set(2, { spreadsheetId: "F", gid: 0 });
    t.failures.set(2, "notfound");
    expect(t.nav({ id: 2, windowId: 7, active: true, url: url("F") })).toBe(false);
    expect(t.failures.get(2)).toBe("notfound");
    expect(t.posted).toHaveLength(0);
    expect(t.nav({ id: 2, windowId: 7, active: true, url: url("G") })).toBe(true);
    expect(t.failures.has(2)).toBe(false);
  });
});
