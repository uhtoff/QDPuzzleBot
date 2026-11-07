import { describe, it, expect } from "vitest";
import { createRegistry } from "../src/lib/registry.js";

const p = (id, spreadsheetId, channel_id = null, extra = {}) => ({ id, name: "P" + id, solved: false, channel_id, spreadsheetId, ...extra });

describe("registry", () => {
  it("sets, gets and deletes", () => {
    const r = createRegistry();
    r.set(1, p(10, "s1"));
    expect(r.get(1).id).toBe(10);
    r.delete(1);
    expect(r.get(1)).toBeUndefined();
  });
  it("findBySpreadsheet matches only the stored spreadsheet", () => {
    const r = createRegistry();
    r.set(1, p(10, "s1"));
    r.set(2, p(11, "s2"));
    expect(r.findBySpreadsheet("s2")).toBe(2);
    expect(r.findBySpreadsheet("s1")).toBe(1);
    expect(r.findBySpreadsheet("nope")).toBeNull();
  });
  it("tabsForChannel returns every tab on that channel", () => {
    const r = createRegistry();
    r.set(1, p(10, "s1", "c1"));
    r.set(2, p(11, "s2", "c2"));
    r.set(3, p(10, "s1", "c1"));
    expect(r.tabsForChannel("c1")).toEqual([1, 3]);
    expect(r.tabsForChannel("zzz")).toEqual([]);
  });
  it("update merges into every entry for that puzzle id, and solvedTabs follows", () => {
    const r = createRegistry();
    r.set(1, p(10, "s1"));
    r.set(2, p(11, "s2"));
    r.set(3, p(10, "s1"));
    expect(r.solvedTabs()).toEqual([]);
    r.update(10, { solved: true });
    expect(r.solvedTabs()).toEqual([1, 3]);
    expect(r.get(1).name).toBe("P10");
    expect(r.get(2).solved).toBe(false);
  });
});
