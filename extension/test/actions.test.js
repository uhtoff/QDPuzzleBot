import { describe, it, expect } from "vitest";
import { validateAction, isNewArrival } from "../src/lib/actions.js";

const ORIGIN = "https://dash.test";
const ctx = { senderId: "me", senderUrl: ORIGIN + "/hunts/x", senderHasTab: true, runtimeId: "me", origin: ORIGIN };
const SHEET = "https://docs.google.com/spreadsheets/d/abc/edit#gid=0";
const work = { action: "work", payload: { sheetUrl: SHEET } };

describe("validateAction", () => {
  it("accepts valid work, openRound, closeSolved", () => {
    expect(validateAction(work, ctx)).toEqual({ ok: true, action: "work", payload: { sheetUrl: SHEET } });
    const r = { action: "openRound", payload: { name: "R", sheetUrls: [SHEET, SHEET] } };
    expect(validateAction(r, ctx)).toEqual({ ok: true, action: "openRound", payload: { name: "R", sheetUrls: [SHEET, SHEET] } });
    expect(validateAction({ action: "closeSolved" }, ctx).ok).toBe(true);
  });
  it("rejects a wrong sender id, wrong origin, missing tab", () => {
    expect(validateAction(work, { ...ctx, senderId: "evil" }).ok).toBe(false);
    expect(validateAction(work, { ...ctx, senderUrl: "https://evil.test/" }).ok).toBe(false);
    expect(validateAction(work, { ...ctx, senderUrl: "https://dash.test.evil.test/" }).ok).toBe(false);
    expect(validateAction(work, { ...ctx, senderUrl: undefined }).ok).toBe(false);
    expect(validateAction(work, { ...ctx, senderHasTab: false }).ok).toBe(false);
  });
  it("rejects unknown actions and non-objects", () => {
    expect(validateAction({ action: "nope", payload: {} }, ctx).ok).toBe(false);
    expect(validateAction(null, ctx).ok).toBe(false);
  });
  it("rejects non-Sheets URLs", () => {
    expect(validateAction({ action: "work", payload: { sheetUrl: "https://evil.test/spreadsheets/d/a" } }, ctx).ok).toBe(false);
    expect(validateAction({ action: "work", payload: { sheetUrl: "javascript:1" } }, ctx).ok).toBe(false);
    expect(validateAction({ action: "openRound", payload: { name: "R", sheetUrls: [SHEET, "https://evil.test/"] } }, ctx).ok).toBe(false);
  });
  it("rejects an openRound with an empty or blank name", () => {
    for (const name of ["", "   "]) {
      expect(validateAction({ action: "openRound", payload: { name, sheetUrls: [SHEET] } }, ctx).ok).toBe(false);
    }
  });
  it("enforces openRound size limits", () => {
    const urls = (n) => Array.from({ length: n }, () => SHEET);
    expect(validateAction({ action: "openRound", payload: { name: "R", sheetUrls: urls(100) } }, ctx).ok).toBe(true);
    expect(validateAction({ action: "openRound", payload: { name: "R", sheetUrls: urls(101) } }, ctx).ok).toBe(false);
    expect(validateAction({ action: "openRound", payload: { name: "x".repeat(101), sheetUrls: [SHEET] } }, ctx).ok).toBe(false);
  });
});

describe("isNewArrival", () => {
  it("is true only when the spreadsheet changed to a known one", () => {
    expect(isNewArrival(undefined, "s1")).toBe(true);
    expect(isNewArrival("s1", "s2")).toBe(true);
    expect(isNewArrival("s1", "s1")).toBe(false);
    expect(isNewArrival("s1", undefined)).toBe(false);
  });
});
