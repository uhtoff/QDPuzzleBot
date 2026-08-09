// One set of status rules. There were two -- util.statusColor (Mantine colour
// names, for badges) and CanvasView.tintFor (hex, for region tints) -- and they
// re-diverged: the tint treats "needs ..." as work in flight, the badge did
// not. statusTone() is the single rule set; each caller maps a tone to its own
// palette. These are the rules, not the palettes.
import { describe, expect, it } from "vitest";
import { statusTone } from "./statusTone";

describe("statusTone", () => {
  it("does not read 'unsolved' as solved", () => {
    // Twice-fixed regression: "unsolved" is a real stored status and CONTAINS
    // "solved", so a substring match gives an unsolved puzzle a green badge.
    expect(statusTone("unsolved")).not.toBe("solved");
    expect(statusTone("Unsolved")).not.toBe("solved");
  });

  it("still matches backsolved, case-insensitively", () => {
    expect(statusTone("backsolved")).toBe("solved");
    expect(statusTone("Solved")).toBe("solved");
    expect(statusTone("solved")).toBe("solved");
  });

  it("treats in-progress and 'needs ...' alike as working", () => {
    // The bot writes constants.py's lowercase values, the dashboard's dropdown
    // writes title case -- both must land on the same tone.
    expect(statusTone("In Progress")).toBe("working");
    expect(statusTone("in progress")).toBe("working");
    // tintFor's rule wins over statusColor's: "needs ..." is work in flight,
    // so these badges deliberately move blue -> yellow.
    expect(statusTone("needs extraction")).toBe("working");
    expect(statusTone("needs submission")).toBe("working");
  });

  it("reads 'partially solved' as work in flight, not solved", () => {
    // It contains "solved" but means the opposite of done. Paired with the
    // backsolved assertions below so a rule that simply stopped matching
    // "solved" at all could not satisfy both.
    expect(statusTone("partially solved")).toBe("working");
    expect(statusTone("Partially Solved")).toBe("working");
    // ...while its neighbours in the same substring family are unaffected:
    expect(statusTone("backsolved")).toBe("solved");
    expect(statusTone("solved")).toBe("solved");
    expect(statusTone("unsolved")).not.toBe("solved");
  });

  it("maps stuck and unstarted to their own tones", () => {
    expect(statusTone("stuck")).toBe("stuck");
    expect(statusTone("Stuck")).toBe("stuck");
    expect(statusTone("unstarted")).toBe("unstarted");
  });

  it("falls back to unknown for empty and unrecognised statuses", () => {
    expect(statusTone("")).toBe("unknown");
    expect(statusTone(null)).toBe("unknown");
    expect(statusTone("weird")).toBe("unknown");
  });
});
