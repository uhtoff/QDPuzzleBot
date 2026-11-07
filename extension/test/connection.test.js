import { describe, it, expect } from "vitest";
import { needsSocket } from "../src/lib/connection.js";

describe("needsSocket", () => {
  it("is false while connecting or open", () => {
    expect(needsSocket(0)).toBe(false);
    expect(needsSocket(1)).toBe(false);
  });
  it("is true when absent, closing or closed", () => {
    expect(needsSocket(undefined)).toBe(true);
    expect(needsSocket(null)).toBe(true);
    expect(needsSocket(2)).toBe(true);
    expect(needsSocket(3)).toBe(true);
  });
});
