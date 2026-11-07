import { describe, it, expect } from "vitest";
import { parseSheetUrl } from "../src/lib/sheetUrl.js";

describe("parseSheetUrl", () => {
  it("parses a files-mode URL without gid", () => {
    expect(parseSheetUrl("https://docs.google.com/spreadsheets/d/abc_123-X/edit")).toEqual({ spreadsheetId: "abc_123-X", gid: null });
  });
  it("reads gid from the hash", () => {
    expect(parseSheetUrl("https://docs.google.com/spreadsheets/d/abc/edit#gid=123")).toEqual({ spreadsheetId: "abc", gid: 123 });
  });
  it("reads gid from the query", () => {
    expect(parseSheetUrl("https://docs.google.com/spreadsheets/d/abc/edit?gid=45")).toEqual({ spreadsheetId: "abc", gid: 45 });
  });
  it("hash gid takes precedence over query gid", () => {
    expect(parseSheetUrl("https://docs.google.com/spreadsheets/d/abc/edit?gid=1#gid=2").gid).toBe(2);
  });
  it("gid 0 is a number, not null", () => {
    expect(parseSheetUrl("https://docs.google.com/spreadsheets/d/abc/edit#gid=0").gid).toBe(0);
  });
  it("parses a Solved spreadsheet URL with extra params", () => {
    expect(parseSheetUrl("https://docs.google.com/spreadsheets/d/solvedId/edit?usp=sharing&gid=9#gid=7")).toEqual({ spreadsheetId: "solvedId", gid: 7 });
  });
  it("returns null for non-Sheets URLs", () => {
    expect(parseSheetUrl("https://example.com/spreadsheets/d/abc/edit")).toBeNull();
    expect(parseSheetUrl("https://docs.google.com/document/d/abc/edit")).toBeNull();
    expect(parseSheetUrl("http://docs.google.com/spreadsheets/d/abc/edit")).toBeNull();
  });
  it("returns null for garbage without throwing", () => {
    expect(parseSheetUrl("not a url")).toBeNull();
    expect(parseSheetUrl("")).toBeNull();
    expect(parseSheetUrl(undefined)).toBeNull();
  });
});
