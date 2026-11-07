import { describe, it, expect } from "vitest";
import { shouldRetryCsrf } from "../src/lib/csrf.js";

const base = { method: "POST", status: 403, error: "csrf", retried: false };

describe("shouldRetryCsrf", () => {
  it("retries a POST refused with csrf, once", () => {
    expect(shouldRetryCsrf(base)).toBe(true);
    expect(shouldRetryCsrf({ ...base, retried: true })).toBe(false);
  });
  it("does not retry other failures", () => {
    expect(shouldRetryCsrf({ ...base, status: 401 })).toBe(false);
    expect(shouldRetryCsrf({ ...base, status: 500 })).toBe(false);
    expect(shouldRetryCsrf({ ...base, error: "forbidden" })).toBe(false);
    expect(shouldRetryCsrf({ ...base, error: undefined })).toBe(false);
  });
  it("never retries a GET", () => {
    expect(shouldRetryCsrf({ ...base, method: "GET" })).toBe(false);
  });
});
