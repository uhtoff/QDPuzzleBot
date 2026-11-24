import { describe, it, expect } from "vitest";
import { createUnread } from "../src/lib/unread.js";

const tracked = (...ids) => (c) => ids.includes(c);

describe("unread", () => {
  it("counts per channel and totals", () => {
    const u = createUnread({ isTracked: tracked("a", "b") });
    expect(u.onMessage("a", [])).toBe(true);
    u.onMessage("a", []);
    u.onMessage("b", []);
    expect(u.count("a")).toBe(2);
    expect(u.count("b")).toBe(1);
    expect(u.total()).toBe(3);
  });
  it("clear removes only that channel", () => {
    const u = createUnread({ isTracked: tracked("a", "b") });
    u.onMessage("a", []);
    u.onMessage("a", []);
    u.onMessage("b", []);
    u.clear("a");
    expect(u.count("a")).toBe(0);
    expect(u.total()).toBe(1);
  });
  it("does not count a channel visible in any open panel", () => {
    const u = createUnread({ isTracked: tracked("a", "b", "c") });
    expect(u.onMessage("b", ["a", "b"])).toBe(false);
    expect(u.onMessage("a", ["a", "b"])).toBe(false);
    expect(u.onMessage("c", ["a", "b"])).toBe(true);
    expect(u.total()).toBe(1);
    expect(u.count("b")).toBe(0);
  });
  it("ignores untracked or missing channels", () => {
    const u = createUnread({ isTracked: tracked("a") });
    expect(u.onMessage("zzz", [])).toBe(false);
    expect(u.onMessage(null, [])).toBe(false);
    expect(u.total()).toBe(0);
  });
  it("only message events count; edits and deletes do not", () => {
    const u = createUnread({ isTracked: tracked("a") });
    expect(u.onEvent({ type: "message_edit", channel_id: "a" }, [])).toBe(false);
    expect(u.onEvent({ type: "message_delete", channel_id: "a" }, [])).toBe(false);
    expect(u.total()).toBe(0);
    expect(u.onEvent({ type: "message", channel_id: "a" }, [])).toBe(true);
    expect(u.total()).toBe(1);
  });
  it("serialise/rehydrate round-trips", () => {
    const u = createUnread({ isTracked: tracked("a", "b") });
    u.onMessage("a", []);
    u.onMessage("a", []);
    u.onMessage("b", []);
    const u2 = createUnread({ isTracked: tracked("a", "b"), initial: JSON.parse(JSON.stringify(u.serialise())) });
    expect(u2.count("a")).toBe(2);
    expect(u2.count("b")).toBe(1);
    expect(u2.total()).toBe(3);
  });
});
