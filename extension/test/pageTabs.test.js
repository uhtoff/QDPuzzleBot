import { describe, it, expect } from "vitest";
import { createPageTabs, pageTabsFor } from "../src/lib/pageTabs.js";

describe("pageTabs", () => {
  it("add then entries round-trips through createPageTabs(entries)", () => {
    const p = createPageTabs();
    p.add(7, { spreadsheetId: "A", gid: 3 });
    const rebuilt = createPageTabs(p.entries());
    expect(pageTabsFor(rebuilt.entries(), "A", 3)).toEqual([7]);
  });

  it("get returns the remembered sheet, or null", () => {
    const p = createPageTabs();
    p.add(7, { spreadsheetId: "A", gid: 3 });
    expect(p.get(7)).toEqual({ spreadsheetId: "A", gid: 3 });
    expect(p.get(8)).toBeNull();
  });

  it("remove forgets the tab", () => {
    const p = createPageTabs();
    p.add(7, { spreadsheetId: "A", gid: 3 });
    expect(p.remove(7)).toBe(true);
    expect(pageTabsFor(p.entries(), "A", 3)).toEqual([]);
    expect(p.remove(7)).toBe(false);
  });

  it("replace moves the record", () => {
    const p = createPageTabs();
    p.add(7, { spreadsheetId: "A", gid: 3 });
    expect(p.replace(7, 9)).toBe(true);
    expect(pageTabsFor(p.entries(), "A", 3)).toEqual([9]);
    expect(p.replace(7, 11)).toBe(false);
  });

  it("sheets dedupes and keeps gid apart", () => {
    const p = createPageTabs();
    p.add(1, { spreadsheetId: "A", gid: 3 });
    p.add(2, { spreadsheetId: "A", gid: 3 });
    p.add(3, { spreadsheetId: "A", gid: 4 });
    p.add(4, { spreadsheetId: "B", gid: null });
    expect(p.sheets()).toHaveLength(3);
    expect(pageTabsFor(p.entries(), "A", 3)).toEqual([1, 2]);
    expect(pageTabsFor(p.entries(), "B", null)).toEqual([4]);
  });
});
