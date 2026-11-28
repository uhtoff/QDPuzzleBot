import { describe, it, expect } from "vitest";
import { parseOrigin, wsUrl, bySheetPath, messagesPath, safeAvatarUrl, huntUnreadPath } from "../src/lib/origin.js";

describe("origin helpers", () => {
  it("huntUnreadPath encodes the hunt uid", () => {
    expect(huntUnreadPath("a b/c")).toBe("/api/hunts/a%20b%2Fc/unread");
  });
  it("accepts bare https origins only", () => {
    expect(parseOrigin("https://puzzbot.com")).toBe("https://puzzbot.com");
    expect(parseOrigin(" https://puzzbot.com/ ")).toBe("https://puzzbot.com");
    expect(parseOrigin("https://x.com:8443")).toBe("https://x.com:8443");
    for (const bad of ["http://x.com", "https://x.com/path", "https://x.com/?a=1", "https://x.com/#h", "https://u:p@x.com", "x.com", ""]) {
      expect(parseOrigin(bad)).toBeNull();
    }
  });
  it("builds ws and api paths", () => {
    expect(wsUrl("https://x.com:8443")).toBe("wss://x.com:8443/api/ws/activity");
    expect(wsUrl("https://x.com", "a+b/c=d&e")).toBe("wss://x.com/api/ws/activity?ticket=a%2Bb%2Fc%3Dd%26e");
    expect(bySheetPath({ spreadsheetId: "a b", gid: 0 })).toBe("/api/puzzles/by-sheet?spreadsheet=a%20b&gid=0");
    expect(bySheetPath({ spreadsheetId: "s", gid: null })).toBe("/api/puzzles/by-sheet?spreadsheet=s");
    expect(messagesPath("123")).toBe("/api/channels/123/messages?limit=50");
  });
  it("allows only https avatars", () => {
    expect(safeAvatarUrl("https://cdn.x/a.png")).toBe("https://cdn.x/a.png");
    expect(safeAvatarUrl("http://cdn.x/a.png")).toBeNull();
    expect(safeAvatarUrl("javascript:alert(1)")).toBeNull();
    expect(safeAvatarUrl(null)).toBeNull();
  });
});

import { removesOrigin } from "../src/lib/origin.js";
describe("removesOrigin", () => {
  it("matches the dashboard origin among removed patterns", () => {
    expect(removesOrigin(["https://other.example/*", "https://a.example/*"], "https://a.example")).toBe(true);
    expect(removesOrigin(["https://b.example/*"], "https://a.example")).toBe(false);
    expect(removesOrigin(undefined, "https://a.example")).toBe(false);
  });
});

describe("SHEETS_ORIGINS", () => {
  it("is exactly the manifest's Google Sheets host permission", async () => {
    const { SHEETS_ORIGINS } = await import("../src/lib/origin.js");
    const { readFileSync } = await import("node:fs");
    const m = JSON.parse(readFileSync(new URL("../manifest.json", import.meta.url), "utf8"));
    expect(SHEETS_ORIGINS).toEqual(m.host_permissions);
    expect(SHEETS_ORIGINS).toEqual(["https://docs.google.com/spreadsheets/*"]);
  });
});

describe("board drawer (?puzzle=)", () => {
  const o = "https://dash.test";
  it("reads the open drawer's puzzle id from a hunt board address", async () => {
    const { parseBoardPuzzleId } = await import("../src/lib/origin.js");
    expect(parseBoardPuzzleId(`${o}/hunts/abc?puzzle=42`, o)).toBe(42);
    expect(parseBoardPuzzleId(`${o}/hunts/abc?image=3&puzzle=7`, o)).toBe(7);
  });
  it("is null with no drawer, a bad id, another page or another site", async () => {
    const { parseBoardPuzzleId } = await import("../src/lib/origin.js");
    for (const url of [`${o}/hunts/abc`, `${o}/hunts/abc?puzzle=x`, `${o}/hunts/abc?puzzle=-1`,
      `${o}/hunts?puzzle=4`, `https://evil.test/hunts/abc?puzzle=4`, "not a url"]) {
      expect(parseBoardPuzzleId(url, o)).toBeNull();
    }
  });
  it("puzzleChatPath names the puzzle", async () => {
    const { puzzleChatPath } = await import("../src/lib/origin.js");
    expect(puzzleChatPath(42)).toBe("/api/puzzles/42/chat");
  });
});
