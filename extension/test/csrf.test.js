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

import { classifyResponse } from "../src/lib/csrf.js";

describe("classifyResponse", () => {
  const b = { method: "POST", status: 200, error: undefined, retried: false };
  it("401 is auth, never retried", () => {
    expect(classifyResponse({ ...b, status: 401 })).toBe("auth");
    expect(classifyResponse({ ...b, method: "GET", status: 401 })).toBe("auth");
    expect(classifyResponse({ ...b, status: 401, error: "csrf" })).toBe("auth");
  });
  it("403 csrf retries once, then errors", () => {
    expect(classifyResponse({ ...b, status: 403, error: "csrf" })).toBe("retry");
    expect(classifyResponse({ ...b, status: 403, error: "csrf", retried: true })).toBe("error");
  });
  it("2xx is ok, other statuses error", () => {
    expect(classifyResponse(b)).toBe("ok");
    expect(classifyResponse({ ...b, status: 500 })).toBe("error");
  });
});
