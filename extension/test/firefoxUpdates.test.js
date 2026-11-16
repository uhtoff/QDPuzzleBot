import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { firefoxManifest } from "../scripts/firefoxManifest.js";

// The file Firefox polls for updates (served by the live dashboard). A malformed entry
// silently stops every Firefox install from updating, so pin its shape here.
const updates = JSON.parse(
  readFileSync(new URL("../../frontend/public/extension/firefox-updates.json", import.meta.url), "utf8"),
);
const chrome = JSON.parse(readFileSync(new URL("../manifest.json", import.meta.url), "utf8"));
const gecko = firefoxManifest(chrome).browser_specific_settings.gecko;

describe("firefox-updates.json", () => {
  it("is served at the manifest's update_url and keyed by the add-on id", () => {
    expect(gecko.update_url).toBe("https://quarantinedecrypters.com/extension/firefox-updates.json");
    expect(Object.keys(updates.addons)).toEqual([gecko.id]);
    expect(Array.isArray(updates.addons[gecko.id].updates)).toBe(true);
  });

  it("lists each signed version with a link beside it and its sha256", () => {
    for (const u of updates.addons[gecko.id].updates) {
      expect(u.version).toMatch(/^\d+\.\d+\.\d+$/);
      expect(u.update_link).toBe(`https://quarantinedecrypters.com/extension/puzzle-chat-firefox-${u.version}.xpi`);
      expect(u.update_hash).toMatch(/^sha256:[0-9a-f]{64}$/);
    }
  });
});
