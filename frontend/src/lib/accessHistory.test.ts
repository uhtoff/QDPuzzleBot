import { describe, expect, it } from "vitest";
import { actionLabel, viaLabel } from "./accessHistory";

describe("viaLabel", () => {
  it.each([
    ["button", "by the Join/Leave button"],
    ["arrival", "on arrival"],
    ["hub_role_lost", "lost the hub role"],
    ["hub_admin_lost", "lost hub admin"],
    ["self", "left on their own"],
  ])("maps %s", (via, words) => {
    expect(viaLabel(via, null)).toBe(words);
  });

  it("names the actor for a hand change", () => {
    expect(viaLabel("hand", "Alice")).toBe("by hand (by Alice)");
  });
  it("omits the actor when unknown", () => {
    expect(viaLabel("hand", null)).toBe("by hand");
  });
  it("ignores an actor for other values", () => {
    expect(viaLabel("button", "Alice")).toBe("by the Join/Leave button");
  });
  it("falls back to the raw string", () => {
    expect(viaLabel("mystery", null)).toBe("mystery");
  });
});

describe("actionLabel", () => {
  it.each([
    ["grant", "got the team role"],
    ["revoke", "lost the team role"],
    ["elevate", "got mod"],
    ["demote", "lost mod"],
    ["arrive", "arrived"],
    ["depart", "left"],
  ])("maps %s", (action, words) => {
    expect(actionLabel(action)).toBe(words);
  });
  it("falls back to the raw string", () => {
    expect(actionLabel("mystery")).toBe("mystery");
  });
});
