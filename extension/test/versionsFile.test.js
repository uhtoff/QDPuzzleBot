import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { compareVersions, updateNotice } from "../src/lib/updateStatus.js";

// versions.json is what every copy (1.2.1+) and the dashboard compare themselves
// against. `latest` must be a RELEASED version: bump it only once the stores have it.
const info = JSON.parse(readFileSync(new URL("../../frontend/public/extension/versions.json", import.meta.url), "utf8"));
const manifest = JSON.parse(readFileSync(new URL("../manifest.json", import.meta.url), "utf8"));

describe("versions.json", () => {
  it("names a latest version that has its own line", () => {
    expect(info.latest).toMatch(/^\d+\.\d+\.\d+$/);
    expect(info.versions.map((v) => v.version)).toContain(info.latest);
  });
  it("every line is a version and a non-empty description", () => {
    for (const v of info.versions) {
      expect(v.version).toMatch(/^\d+\.\d+\.\d+$/);
      expect(typeof v.adds).toBe("string");
      expect(v.adds.trim()).not.toBe("");
    }
  });
  it("is never ahead of the source: a release bumps the manifest first", () => {
    expect(compareVersions(info.latest, manifest.version)).toBeLessThanOrEqual(0);
  });
  it("this source tells nobody to update", () => {
    expect(updateNotice(manifest.version, info, null)).toBeNull();
  });
});
