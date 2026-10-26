import { describe, expect, it } from "vitest";
import { DEFAULT_SITE, pageTitle } from "./siteInfo";

describe("pageTitle", () => {
  it("names the instance and marks a test instance", () => {
    expect(pageTitle({ name: "Puzzbot", test_instance: true, contact: "" })).toBe(
      "Puzzbot (test) — puzzle-hunt team dashboard",
    );
  });

  it("leaves a live instance unmarked", () => {
    expect(pageTitle({ name: "QD Puzzbot", test_instance: false, contact: "" })).toBe(
      "QD Puzzbot — puzzle-hunt team dashboard",
    );
  });

  it("falls back to the default name when none is configured", () => {
    expect(pageTitle({ ...DEFAULT_SITE, name: "  " })).toBe("Puzzbot — puzzle-hunt team dashboard");
  });
});
