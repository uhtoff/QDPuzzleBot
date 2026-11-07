import { describe, it, expect } from "vitest";
import { createNameCache, resolveName } from "../src/lib/channelNames.js";

function setup(opts = {}) {
  const clock = { t: 1000 };
  const calls = [];
  let result = { 1: "Alpha" };
  let fail = false;
  const cache = createNameCache({
    now: () => clock.t,
    fetcher: async (h) => {
      calls.push(h);
      if (fail) throw new Error("x");
      return result;
    },
    ...opts,
  });
  return { clock, calls, cache, set: (r) => (result = r), failing: (f) => (fail = f) };
}

describe("createNameCache", () => {
  it("fetches once, then hits", async () => {
    const s = setup();
    expect(await s.cache.ensure("h", "1")).toEqual({ 1: "Alpha" });
    await s.cache.ensure("h", "1");
    expect(s.calls.length).toBe(1);
  });
  it("miss refetches, but throttled to the gap", async () => {
    const s = setup();
    await s.cache.ensure("h", "1");
    s.set({ 1: "Alpha", 2: "Beta" });
    s.clock.t += 10000;
    expect((await s.cache.ensure("h", "2"))[2]).toBeUndefined();
    expect(s.calls.length).toBe(1);
    s.clock.t += 21000;
    expect((await s.cache.ensure("h", "2"))[2]).toBe("Beta");
    expect(s.calls.length).toBe(2);
  });
  it("TTL expiry refetches", async () => {
    const s = setup();
    await s.cache.ensure("h", "1");
    s.clock.t += 61000;
    await s.cache.ensure("h", "1");
    expect(s.calls.length).toBe(2);
  });
  it("fetch failure keeps the old map and does not hammer", async () => {
    const s = setup();
    await s.cache.ensure("h", "1");
    s.clock.t += 61000;
    s.failing(true);
    expect(await s.cache.ensure("h", "1")).toEqual({ 1: "Alpha" });
    expect(await s.cache.ensure("h", "1")).toEqual({ 1: "Alpha" });
    expect(s.calls.length).toBe(2);
  });
  it("first-fetch failure gives empty map", async () => {
    const s = setup();
    s.failing(true);
    expect(await s.cache.ensure("h", "1")).toEqual({});
  });
  it("hunts are independent; no hunt gives empty", async () => {
    const s = setup();
    await s.cache.ensure("a", "1");
    await s.cache.ensure("b", "1");
    expect(s.calls).toEqual(["a", "b"]);
    expect(await s.cache.ensure(undefined, "1")).toEqual({});
  });
});

describe("single-flight", () => {
  it("concurrent cold calls share one fetch; after TTL, one more", async () => {
    const s = setup();
    const r = await Promise.all([1, 2, 3, 4, 5].map(() => s.cache.ensure("h", "1")));
    expect(s.calls.length).toBe(1);
    expect(r.every((m) => m === r[0])).toBe(true);
    s.clock.t += 61000;
    await Promise.all([1, 2, 3].map(() => s.cache.ensure("h", "1")));
    expect(s.calls.length).toBe(2);
  });
  it("cached() is sync and empty before the first fetch", async () => {
    const s = setup();
    expect(s.cache.cached("h")).toEqual({});
    await s.cache.ensure("h");
    expect(s.cache.cached("h")).toEqual({ 1: "Alpha" });
  });
});

describe("resolveName", () => {
  const reg = (id) => ({ 2: "FromTab", 1: "TabAlpha" })[id];
  it("map, then registry, then null", () => {
    expect(resolveName("1", { 1: "Alpha" }, reg)).toBe("Alpha");
    expect(resolveName("2", { 1: "Alpha" }, reg)).toBe("FromTab");
    expect(resolveName("3", { 1: "Alpha" }, reg)).toBeNull();
    expect(resolveName("3", undefined, undefined)).toBeNull();
  });
});

describe("createNameCache.clear", () => {
  it("drops cached maps and ignores a load that was in flight at clear time", async () => {
    let release;
    let calls = 0;
    const cache = createNameCache({
      fetcher: () => {
        calls++;
        return new Promise((r) => (release = r));
      },
    });
    const inflight = cache.ensure("h1");
    cache.clear();
    release({ 1: "old-origin" });
    await inflight;
    expect(cache.cached("h1")).toEqual({}); // stale result not stored
    const next = cache.ensure("h1"); // fresh fetch, not the pending old one
    release({ 1: "new-origin" });
    expect(await next).toEqual({ 1: "new-origin" });
    expect(calls).toBe(2);
  });
});
