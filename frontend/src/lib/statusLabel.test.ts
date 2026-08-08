import { describe, it, expect } from "vitest";
import { formatStatus, formatPriority } from "./statusLabel";

describe("formatStatus", () => {
  it("title-cases every word of a stored lowercase status", () => {
    // Multi-word values are the whole point: a naive capitalise-first-letter
    // would render "In progress" / "Needs submission".
    expect(formatStatus("in progress")).toBe("In Progress");
    expect(formatStatus("needs submission")).toBe("Needs Submission");
    expect(formatStatus("needs extraction")).toBe("Needs Extraction");
    expect(formatStatus("partially solved")).toBe("Partially Solved");
    expect(formatStatus("stuck")).toBe("Stuck");
  });

  it("leaves pre-backfill Title Case rows unchanged", () => {
    expect(formatStatus("In Progress")).toBe("In Progress");
    expect(formatStatus("Unsolved")).toBe("Unsolved");
  });

  it("renders an absent status as empty, so callers can fall back", () => {
    expect(formatStatus(null)).toBe("");
    expect(formatStatus("")).toBe("");
  });
});

describe("formatPriority", () => {
  it("title-cases multi-word priorities", () => {
    expect(formatPriority("very high")).toBe("Very High");
    expect(formatPriority("low")).toBe("Low");
  });

  it("renders an absent priority as empty", () => {
    expect(formatPriority(null)).toBe("");
  });
});
