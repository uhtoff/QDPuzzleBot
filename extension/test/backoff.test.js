import { describe, it, expect } from "vitest";
import { backoffDelay, reconnectVia, resolveAttempt } from "../src/lib/backoff.js";

describe("backoffDelay", () => {
  it("doubles then caps", () => {
    expect([0, 1, 2, 3].map((n) => backoffDelay(n))).toEqual([1000, 2000, 4000, 8000]);
    expect(backoffDelay(10)).toBe(60000);
    expect(backoffDelay(100)).toBe(60000);
  });
});

describe("reconnectVia", () => {
  it("uses alarms from 30s up", () => {
    expect(reconnectVia(16000)).toBe("timeout");
    expect(reconnectVia(29999)).toBe("timeout");
    expect(reconnectVia(30000)).toBe("alarm");
    expect(reconnectVia(60000)).toBe("alarm");
  });
});

describe("resolveAttempt", () => {
  it("resumes a persisted counter, defaulting bad values to 0", () => {
    expect(resolveAttempt(4)).toBe(4);
    expect(resolveAttempt(undefined)).toBe(0);
    expect(resolveAttempt(-1)).toBe(0);
    expect(resolveAttempt("3")).toBe(0);
  });
});
