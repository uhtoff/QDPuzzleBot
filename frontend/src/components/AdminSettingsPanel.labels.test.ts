import { describe, expect, it } from "vitest";
import { HEADER_STYLE_OPTIONS, settingLabel } from "./AdminSettingsPanel";

describe("settingLabel", () => {
  it("names saved settings as the form labels them", () => {
    expect(settingLabel("voice_per_puzzle")).toBe("voice channel for every new puzzle");
    expect(settingLabel("start_timestamp")).toBe("start time");
    expect(settingLabel("url")).toBe("website URL");
  });
  it("labels the header style setting and its choices", () => {
    expect(settingLabel("header_style")).toBe("header style");
    expect(HEADER_STYLE_OPTIONS.map((o) => [o.value, o.label])).toEqual([
      ["compact", "Compact header"],
      ["full", "Full header"],
    ]);
  });
  it("falls back to spaced words for an unknown key", () => {
    expect(settingLabel("some_new_field")).toBe("some new field");
  });
});
