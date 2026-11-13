import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { firefoxManifest } from "../scripts/firefoxManifest.js";

const chrome = JSON.parse(readFileSync(new URL("../manifest.json", import.meta.url), "utf8"));

describe("firefoxManifest", () => {
  const ff = firefoxManifest(chrome);

  it("drops the Chrome-only keys and the sidePanel permission, keeping order", () => {
    for (const k of ["side_panel", "minimum_chrome_version", "options_page"]) expect(ff).not.toHaveProperty(k);
    expect(ff.permissions).toEqual(chrome.permissions.filter((p) => p !== "sidePanel"));
    expect(ff.permissions).not.toContain("sidePanel");
    expect(ff.permissions.length).toBe(chrome.permissions.length - 1);
  });

  it("runs the background as a module event page", () => {
    expect(ff.background).toEqual({ scripts: ["src/background.js"], type: "module" });
  });

  it("declares the sidebar and the options page", () => {
    expect(ff.sidebar_action).toEqual({
      default_panel: "src/panel.html",
      default_title: "Puzzle Chat",
      default_icon: chrome.action.default_icon,
      open_at_install: false,
    });
    expect(ff.options_ui).toEqual({ page: "src/options.html", open_in_tab: true });
  });

  it("sets the gecko id, floor and data-collection declaration", () => {
    expect(ff.browser_specific_settings).toEqual({
      gecko: {
        id: "puzzle-chat@puzzbot.com",
        strict_min_version: "140.0",
        data_collection_permissions: {
          required: ["authenticationInfo", "websiteContent", "personalCommunications"],
        },
      },
    });
  });

  it("passes the shared keys through unchanged", () => {
    for (const k of ["manifest_version", "name", "version", "description", "icons", "action",
      "host_permissions", "optional_host_permissions", "optional_permissions"]) {
      expect(ff[k]).toEqual(chrome[k]);
    }
  });

  it("does not mutate its input", () => {
    const before = structuredClone(chrome);
    const out = firefoxManifest(chrome);
    expect(chrome).toEqual(before);
    out.action.default_icon["16"] = "x";
    out.sidebar_action.default_icon["16"] = "y";
    expect(chrome).toEqual(before);
  });
});
