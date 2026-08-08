import { describe, expect, it } from "vitest";
import { safeHref } from "./safeHref";

describe("safeHref", () => {
  it("passes through ordinary web links", () => {
    expect(safeHref("https://example.test/a")).toBe("https://example.test/a");
    expect(safeHref("http://example.test/a")).toBe("http://example.test/a");
  });

  it("rejects script-bearing schemes", () => {
    // These are the payloads: rendered into an href they run on click, with
    // the dashboard's session. Embed urls are attacker-settable via Discord.
    expect(safeHref("javascript:alert(1)")).toBeUndefined();
    expect(safeHref("JavaScript:alert(1)")).toBeUndefined();
    expect(safeHref("data:text/html,<script>alert(1)</script>")).toBeUndefined();
    expect(safeHref("vbscript:msgbox(1)")).toBeUndefined();
  });

  it("rejects a scheme smuggled past a naive prefix check", () => {
    // Leading whitespace/control characters are stripped by the browser before
    // it resolves the scheme, so a startsWith("http") test would accept this.
    expect(safeHref(" \tjavascript:alert(1)")).toBeUndefined();
  });

  it("allows a relative url on our own origin", () => {
    expect(safeHref("/api/media/7/raw")).toBe("/api/media/7/raw");
  });

  it("returns undefined for a missing or unparseable url", () => {
    expect(safeHref(null)).toBeUndefined();
    expect(safeHref(undefined)).toBeUndefined();
    expect(safeHref("")).toBeUndefined();
  });
});
