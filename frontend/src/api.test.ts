import { describe, expect, it } from "vitest";
import { friendlyError } from "./api";

describe("friendlyError sheet-mode slugs", () => {
  it("explains each sheet-mode refusal in plain English", () => {
    expect(friendlyError({ error: "sheet_mode_locked" }, 409)).toBe(
      "This hunt already has puzzles or groups, so its sheet mode can't be changed.",
    );
    expect(friendlyError({ error: "files_mode_no_solved_sheet" }, 409)).toBe(
      "Hunts with one spreadsheet per puzzle don't use a solved spreadsheet.",
    );
    expect(friendlyError({ error: "bad_sheet_mode" }, 400)).toMatch(/tabs or files/);
    expect(friendlyError({ error: "sheet_mode_failed" }, 502)).toMatch(/sheet mode/);
  });

  it("still falls back to the raw slug for an unknown error", () => {
    expect(friendlyError({ error: "something_new" }, 500)).toBe("something_new");
  });
});

describe("friendlyError teams_still_playing", () => {
  it("shows the server's message, with a fallback", () => {
    const msg = "2 team(s) are still playing **Big Hunt** — archive their servers to the hub first.";
    expect(friendlyError({ error: "teams_still_playing", message: msg }, 409)).toBe(msg);
    expect(friendlyError({ error: "teams_still_playing" }, 409)).toMatch(/archive their servers/);
  });
});

describe("friendlyError voice slugs", () => {
  it("explains each create-voice refusal", () => {
    expect(friendlyError({ error: "solved" }, 409)).toBe("This puzzle is solved — no new voice channel.");
    expect(friendlyError({ error: "no_hunt_category" }, 409)).toBe(
      "Can't find the hunt's category to copy permissions from.",
    );
    expect(friendlyError({ error: "archived" }, 409)).toMatch(/archived/);
  });
});
