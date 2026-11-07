import { describe, it, expect } from "vitest";
import { shouldApplyLookup, shouldCloseTab, planWindowClose } from "../src/lib/closeSolved.js";

const url = (id) => "https://docs.google.com/spreadsheets/d/" + id + "/edit#gid=0";

describe("shouldApplyLookup", () => {
  it("does not resurrect a tab closed meanwhile", () => {
    expect(shouldApplyLookup(undefined, "A")).toBe(false);
  });
  it("skips when the tab now holds another spreadsheet", () => {
    expect(shouldApplyLookup({ spreadsheetId: "B" }, "A")).toBe(false);
    expect(shouldApplyLookup({ spreadsheetId: "A" }, "A")).toBe(true);
  });
});

describe("shouldCloseTab", () => {
  it("closes only a tab still on the solved spreadsheet", () => {
    expect(shouldCloseTab({ url: url("A") }, "A")).toBe(true);
    expect(shouldCloseTab({ url: url("B") }, "A")).toBe(false);
    expect(shouldCloseTab({ url: "https://example.com/" }, "A")).toBe(false);
    expect(shouldCloseTab(null, "A")).toBe(false);
  });
});

describe("planWindowClose", () => {
  const t = (id, windowId) => ({ id, windowId });
  it("needs a blank tab only when every tab in the window closes", () => {
    expect(planWindowClose([t(1, 7), t(2, 7)], [t(1, 7), t(2, 7)], 7)).toEqual({ closing: [1, 2], needBlank: true });
    expect(planWindowClose([t(1, 7), t(2, 7), t(3, 7)], [t(1, 7), t(2, 7)], 7)).toEqual({ closing: [1, 2], needBlank: false });
  });
  it("ignores a candidate dragged to another window", () => {
    expect(planWindowClose([t(1, 7), t(2, 7)], [t(1, 7), t(2, 9)], 7)).toEqual({ closing: [1], needBlank: false });
  });
  it("closes nothing when no candidates remain", () => {
    expect(planWindowClose([t(1, 7)], [], 7)).toEqual({ closing: [], needBlank: false });
  });
});
