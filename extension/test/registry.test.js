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

describe("registry persistence", () => {
  it("entries() returns serialisable pairs and createRegistry(initial) restores them", () => {
    const r = createRegistry();
    r.set(1, p(10, "s1"));
    r.set(2, p(11, "s2"));
    const round = JSON.parse(JSON.stringify(r.entries()));
    expect(round).toEqual([[1, p(10, "s1")], [2, p(11, "s2")]]);
    const r2 = createRegistry(round);
    expect(r2.get(2).id).toBe(11);
    expect(r2.findBySpreadsheet("s1")).toBe(1);
    expect(createRegistry().entries()).toEqual([]);
  });

  describe("reuseTarget", () => {
    const f = (id, ss) => p(id, ss, null, { sheet_mode: "files" });
    it("returns the other tab holding the same files-mode spreadsheet", () => {
      const r = createRegistry();
      r.set(1, f(10, "s1"));
      r.set(2, f(11, "s2"));
      r.set(3, f(10, "s1"));
      expect(r.reuseTarget(3, "s1", 2)).toBe(1);
      expect(r.reuseTarget(1, "s1", 2)).toBe(3);
    });
    it("is null for the only tab in a window", () => {
      const r = createRegistry();
      r.set(1, f(10, "s1"));
      r.set(3, f(10, "s1"));
      expect(r.reuseTarget(3, "s1", 1)).toBeNull();
    });
    it("is null for the same tab, an unknown sheet, or tabs-mode puzzles", () => {
      const r = createRegistry();
      r.set(1, f(10, "s1"));
      expect(r.reuseTarget(1, "s1", 3)).toBeNull();
      expect(r.reuseTarget(9, "nope", 3)).toBeNull();
      const t = createRegistry();
      t.set(1, p(10, "s1", null, { sheet_mode: "tabs" }));
      t.set(2, p(11, "s1", null, { sheet_mode: "tabs" }));
      expect(t.reuseTarget(2, "s1", 3)).toBeNull();
    });
    it("findFilesTab ignores tabs-mode entries and the excluded tab", () => {
      const r = createRegistry();
      r.set(1, p(10, "s1", null, { sheet_mode: "tabs" }));
      r.set(2, f(11, "s2"));
      expect(r.findFilesTab("s1")).toBeNull();
      expect(r.findFilesTab("s2")).toBe(2);
      expect(r.findFilesTab("s2", 2)).toBeNull();
    });
  });
});
