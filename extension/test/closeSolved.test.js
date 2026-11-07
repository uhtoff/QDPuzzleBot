import { describe, it, expect } from "vitest";
import { shouldApplyLookup, shouldCloseTab } from "../src/lib/closeSolved.js";

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
