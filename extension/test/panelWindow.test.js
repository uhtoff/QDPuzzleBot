import { describe, it, expect } from "vitest";
import { panelWindow } from "../src/lib/panelWindow.js";
import { createRegistry } from "../src/lib/registry.js";

describe("panelWindow", () => {
  it("uses the existing tab's window, not the sender's", () => {
    expect(panelWindow({ windowId: 5 }, 9)).toBe(5);
  });
  it("falls back to the sender's window when nothing is known", () => {
    expect(panelWindow(null, 9)).toBe(9);
    expect(panelWindow(undefined, 9)).toBe(9);
    expect(panelWindow({}, 9)).toBe(9);
  });
});

describe("registry.setWindow", () => {
  it("records a window change for a known tab only", () => {
    const r = createRegistry([[1, { spreadsheetId: "s", windowId: 2 }]]);
    expect(r.setWindow(1, 3)).toBe(true);
    expect(r.get(1).windowId).toBe(3);
    expect(r.setWindow(1, 3)).toBe(false);
    expect(r.setWindow(99, 3)).toBe(false);
    expect(r.get(99)).toBeUndefined();
  });
});
