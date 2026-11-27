import { describe, it, expect } from "vitest";
import { compareVersions, updateNotice } from "../src/lib/updateStatus.js";

// versions.json (frontend/public/extension/) lists the newest RELEASED version and
// what each version adds; a copy older than `latest` says so, once per release.
const INFO = {
  latest: "1.3.0",
  versions: [
    { version: "1.3.0", adds: "the version notice" },
    { version: "1.2.1", adds: "three fixes" },
    { version: "1.2.0", adds: "the unread puzzles list" },
  ],
};

describe("compareVersions", () => {
  it("compares numerically, part by part", () => {
    expect(compareVersions("1.2.1", "1.2.0")).toBe(1);
    expect(compareVersions("1.2.0", "1.10.0")).toBe(-1); // not as strings
    expect(compareVersions("1.2", "1.2.0")).toBe(0);
    expect(compareVersions("2.0.0", "1.99.9")).toBe(1);
  });
});

describe("updateNotice", () => {
  it("an older copy lists what each newer release adds, newest first", () => {
    expect(updateNotice("1.2.0", INFO, null)).toEqual({
      latest: "1.3.0",
      adds: [
        { version: "1.3.0", adds: "the version notice" },
        { version: "1.2.1", adds: "three fixes" },
      ],
    });
  });
  it("an up-to-date or newer copy (a test build) has no notice", () => {
    expect(updateNotice("1.3.0", INFO, null)).toBeNull();
    expect(updateNotice("1.4.0", INFO, null)).toBeNull();
  });
  it("dismissing hides it until a newer release", () => {
    expect(updateNotice("1.2.0", INFO, "1.3.0")).toBeNull();
    expect(updateNotice("1.2.0", INFO, "1.2.1")).not.toBeNull();
  });
  it("a missing or malformed file shows nothing", () => {
    for (const bad of [null, undefined, {}, { latest: 3 }, { latest: "x.y" }, "nope"]) {
      expect(updateNotice("1.2.0", bad, null)).toBeNull();
    }
  });
  it("a release without a line still counts as an update", () => {
    expect(updateNotice("1.2.0", { latest: "1.2.1" }, null)).toEqual({ latest: "1.2.1", adds: [] });
  });
});
