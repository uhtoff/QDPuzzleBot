import { describe, it, expect } from "vitest";
import { DISCORD_EPOCH_MS, snowflakeAt, maxId, createMarkers } from "../src/lib/readMarkers.js";

const T = 1700000000000; // 2023-11-14T22:13:20Z
const snow = (ms) => String((BigInt(ms) - 1420070400000n) << 22n);

describe("snowflakeAt", () => {
  it("encodes a time as a Discord snowflake string", () => {
    expect(DISCORD_EPOCH_MS).toBe(1420070400000n);
    expect(snowflakeAt(T)).toBe(snow(T));
    expect(BigInt(snowflakeAt(T + 1000)) > BigInt(snowflakeAt(T))).toBe(true);
  });
});

describe("maxId", () => {
  it("compares numerically, not as strings", () => {
    expect(maxId("9", "10")).toBe("10");
    expect(maxId("10", "9")).toBe("10");
  });
  it("ignores empty values and is null when nothing is left", () => {
    expect(maxId(null, "5", undefined, "")).toBe("5");
    expect(maxId(null, undefined, "")).toBeNull();
  });
  it("is exact beyond 2^53", () => {
    expect(maxId("9007199254740992", "9007199254740993")).toBe("9007199254740993");
  });
});

describe("createMarkers", () => {
  it("sets a baseline once, on first run only", () => {
    const m = createMarkers();
    expect(m.ensureBaseline(T)).toBe(true);
    expect(m.state().baseline).toBe(snow(T));
    expect(m.ensureBaseline(T + 60000)).toBe(false);
    expect(m.state().baseline).toBe(snow(T));
  });
  it("keeps a stored baseline", () => {
    const m = createMarkers({ baseline: "100", read: {} });
    expect(m.ensureBaseline(T)).toBe(false);
    expect(m.state()).toEqual({ baseline: "100", read: {} });
  });
  it("effective is the larger of baseline and the channel's own marker", () => {
    const m = createMarkers({ baseline: "100", read: { a: "150", b: "50" } });
    expect(m.effective("a")).toBe("150");
    expect(m.effective("b")).toBe("100");
    expect(m.effective("c")).toBe("100");
  });
  it("look stores now when it is later than the newest message", () => {
    const m = createMarkers({ baseline: "100", read: {} });
    expect(m.look("a", "120", T)).toBe(true);
    expect(m.effective("a")).toBe(snow(T));
    expect(m.effective("b")).toBe("100");
  });
  it("look stores the newest message when it is later than now", () => {
    const m = createMarkers({ baseline: "100", read: {} });
    const later = String(BigInt(snow(T)) + 5n);
    m.look("a", later, T);
    expect(m.effective("a")).toBe(later);
  });
  it("look reports no change when nothing moves", () => {
    const m = createMarkers({ baseline: "100", read: {} });
    m.look("a", "120", T);
    expect(m.look("a", "120", T)).toBe(false);
  });
  it("isUnread compares against the effective marker, exactly", () => {
    const m = createMarkers({ baseline: "100", read: { big: "9007199254740992" } });
    expect(m.isUnread("x", "101")).toBe(true);
    expect(m.isUnread("x", "100")).toBe(false);
    expect(m.isUnread("big", "9007199254740993")).toBe(true);
    expect(m.isUnread("big", "9007199254740992")).toBe(false);
  });
  it("markAll raises the baseline, empties read, and returns what it replaced", () => {
    const m = createMarkers({ baseline: "100", read: { a: "150", b: "160" } });
    const prev = m.markAll(T);
    expect(prev).toEqual({ baseline: "100", read: { a: "150", b: "160" } });
    expect(m.state()).toEqual({ baseline: snow(T), read: {} });
    m.restore(prev);
    expect(m.effective("a")).toBe("150");
    expect(m.effective("b")).toBe("160");
    expect(m.state().baseline).toBe("100");
  });
  it("markAll's returned state is a copy, not the live one", () => {
    const m = createMarkers({ baseline: "100", read: { a: "150" } });
    const prev = m.markAll(T);
    m.look("b", "1", T + 1000);
    expect(prev.read).toEqual({ a: "150" });
  });
  it("request drops read entries at or below the baseline", () => {
    const m = createMarkers({ baseline: "100", read: { a: "150", b: "100", c: "50" } });
    expect(m.request()).toEqual({ baseline: "100", read: { a: "150" } });
  });
});
