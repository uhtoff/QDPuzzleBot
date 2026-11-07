import { describe, it, expect } from "vitest";
import { createLookupGen } from "../src/lib/lookupGen.js";

describe("createLookupGen", () => {
  it("discards an older lookup once a newer one began for the same tab", () => {
    const g = createLookupGen();
    const x = g.begin(1);
    const y = g.begin(1);
    expect(g.isCurrent(1, x)).toBe(false);
    expect(g.isCurrent(1, y)).toBe(true);
  });
  it("X->Y with X resolving last keeps Y", () => {
    const g = createLookupGen();
    const reg = new Map();
    const resolve = (tab, tok, v) => g.isCurrent(tab, tok) && reg.set(tab, v);
    const x = g.begin(1);
    const y = g.begin(1);
    resolve(1, y, "Y");
    resolve(1, x, "X");
    expect(reg.get(1)).toBe("Y");
  });
  it("tabs are independent and drop invalidates", () => {
    const g = createLookupGen();
    const a = g.begin(1);
    const b = g.begin(2);
    g.drop(1);
    expect(g.isCurrent(1, a)).toBe(false);
    expect(g.isCurrent(2, b)).toBe(true);
  });
});
