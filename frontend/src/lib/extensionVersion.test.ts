// The dashboard tells someone whose Puzzle Chat copy is behind the newest RELEASED
// version (public/extension/versions.json). Copies up to 1.2.0 publish no version.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { compareVersions, extensionUpdateNotice, installedExtensionVersion } from "./extensionVersion";

const dataset: Record<string, string> = {};
beforeEach(() => {
  for (const k of Object.keys(dataset)) delete dataset[k];
  vi.stubGlobal("document", { documentElement: { dataset } });
});
afterEach(() => vi.unstubAllGlobals());

const INFO = {
  latest: "1.2.1",
  versions: [
    { version: "1.2.1", adds: "fixes and this notice" },
    { version: "1.2.0", adds: "the unread list" },
  ],
};

describe("installedExtensionVersion", () => {
  it("is null without the extension", () => {
    expect(installedExtensionVersion()).toBeNull();
  });
  it("a copy that publishes no version is taken as 1.2.0", () => {
    dataset.puzzbotExtension = "1";
    expect(installedExtensionVersion()).toBe("1.2.0");
  });
  it("a copy that publishes one is taken at its word", () => {
    dataset.puzzbotExtension = "1";
    dataset.puzzbotVersion = "1.2.1";
    expect(installedExtensionVersion()).toBe("1.2.1");
  });
});

describe("compareVersions", () => {
  it("compares numerically", () => {
    expect(compareVersions("1.10.0", "1.2.0")).toBe(1);
    expect(compareVersions("1.2", "1.2.0")).toBe(0);
  });
});

describe("extensionUpdateNotice", () => {
  it("an older copy lists what it would gain", () => {
    expect(extensionUpdateNotice("1.2.0", INFO, null)).toEqual({
      latest: "1.2.1",
      adds: [{ version: "1.2.1", adds: "fixes and this notice" }],
    });
  });
  it("no extension, an up-to-date copy, or a dismissed release: nothing", () => {
    expect(extensionUpdateNotice(null, INFO, null)).toBeNull();
    expect(extensionUpdateNotice("1.2.1", INFO, null)).toBeNull();
    expect(extensionUpdateNotice("1.2.0", INFO, "1.2.1")).toBeNull();
  });
  it("a malformed file shows nothing", () => {
    expect(extensionUpdateNotice("1.2.0", { latest: "soon" }, null)).toBeNull();
    expect(extensionUpdateNotice("1.2.0", null, null)).toBeNull();
  });
});
