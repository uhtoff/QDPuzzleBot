import { describe, it, expect } from "vitest";
import { parseOrigin, wsUrl, bySheetPath, messagesPath, safeAvatarUrl } from "../src/lib/origin.js";

describe("origin helpers", () => {
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
