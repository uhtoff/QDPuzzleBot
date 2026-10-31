import { describe, expect, it } from "vitest";
import { actionLabel, historyLine, viaLabel } from "./accessHistory";

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

describe("historyLine", () => {
  const row = (name: string, action: string, via: string, actor: string | null = null) => ({
    at: "2026-10-31T00:00:00Z",
    user_id: "1",
    name,
    action,
    via,
    actor_id: null,
    actor_name: actor,
  });
  it.each([
    [row("Fay", "grant", "arrival"), "Fay got the team role (on arrival)."],
    [row("Fay", "grant", "button"), "Fay got the team role (by the Join/Leave button)."],
    [row("Fay", "revoke", "button"), "Fay lost the team role (by the Join/Leave button)."],
    [row("Fay", "elevate", "button"), "Fay got mod (by the Join/Leave button)."],
    [row("Fay", "demote", "button"), "Fay lost mod (by the Join/Leave button)."],
    [row("Bo", "elevate", "hand", "Russ"), "Bo got mod (by hand, by Russ)."],
    [row("Bo", "elevate", "hand"), "Bo got mod (by hand)."],
    [row("Bo", "revoke", "hub_role_lost"), "Bo lost the team role (lost the hub role)."],
    [row("Bo", "demote", "hub_admin_lost"), "Bo lost mod (lost hub admin)."],
    [row("Bo", "depart", "hub_role_lost"), "Bo left (lost the hub role)."],
    [row("Bo", "depart", "hand", "Russ"), "Bo left (by hand, by Russ)."],
    [row("Bo", "depart", "self"), "Bo left."],
    [row("Bo", "revoke", "self"), "Bo lost the team role (when they left)."],
    [row("Bo", "demote", "self"), "Bo lost mod (when they left)."],
    [row("Fay", "arrive", "arrival"), "Fay arrived."],
  ])("renders %j", (r, sentence) => {
    expect(historyLine(r)).toBe(sentence);
  });
});
