import { describe, it, expect } from "vitest";
import { backoffDelay } from "../src/lib/backoff.js";

describe("backoffDelay", () => {
  it("doubles then caps", () => {
    expect([0, 1, 2, 3].map((n) => backoffDelay(n))).toEqual([1000, 2000, 4000, 8000]);
    expect(backoffDelay(10)).toBe(60000);
    expect(backoffDelay(100)).toBe(60000);
  });
});

import { reconnectVia } from "../src/lib/backoff.js";
describe("reconnectVia", () => {
  it("uses alarms from 30s up", () => {
    expect(reconnectVia(16000)).toBe("timeout");
    expect(reconnectVia(29999)).toBe("timeout");
    expect(reconnectVia(30000)).toBe("alarm");
    expect(reconnectVia(60000)).toBe("alarm");
  });
});
