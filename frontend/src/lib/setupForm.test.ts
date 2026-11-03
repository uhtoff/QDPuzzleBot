import { describe, expect, it } from "vitest";
import {
  buildPayload,
  canSubmit,
  emptySetupForm,
  multiTeamMatch,
  pickRunningHunt,
  type SetupForm,
} from "./setupForm";

const filled = (over: Partial<SetupForm> = {}): SetupForm => ({
  ...emptySetupForm(0),
  hubId: "11",
  roleId: "22",
  huntName: "Mystery Hunt",
  huntUrl: "https://hunt.test",
  ...over,
});

describe("buildPayload", () => {
  it("omits blank optionals and defaults send_invites to false", () => {
    expect(buildPayload(filled({ username: "  ", password: "" }))).toEqual({
      hub_id: "11",
      roster_role_id: "22",
      hunt_name: "Mystery Hunt",
      hunt_url: "https://hunt.test",
      send_invites: false,
    });
  });

  it("trims strings and includes optionals that are set", () => {
    const p = buildPayload(
      filled({
        huntName: "  Hunt  ",
        huntUrl: " https://x.test ",
        username: " u ",
        password: "p",
        createSolvedSheet: true,
        sendInvites: true,
      }),
    );
    expect(p).toMatchObject({
      hunt_name: "Hunt",
      hunt_url: "https://x.test",
      username: "u",
      password: "p",
      create_solved_sheet: true,
      send_invites: true,
    });
  });

  it("sends files mode and drops the solved-sheet option with it", () => {
    const files = buildPayload(filled({ filesMode: true, createSolvedSheet: true }));
    expect(files.sheet_mode).toBe("files");
    expect(files).not.toHaveProperty("create_solved_sheet");
    const tabs = buildPayload(filled({ createSolvedSheet: true }));
    expect(tabs).not.toHaveProperty("sheet_mode");
    expect(tabs.create_solved_sheet).toBe(true);
  });

  it("converts the start time using the chosen offset", () => {
    const at = (offset: number) =>
      buildPayload(filled({ startLocal: "2026-01-16T12:00", startOffsetMinutes: offset }))
        .start_timestamp;
    expect(at(0)).toBe(Date.UTC(2026, 0, 16, 12, 0) / 1000);
    expect(at(120)).toBe(Date.UTC(2026, 0, 16, 10, 0) / 1000);
    expect(at(-300)).toBe(Date.UTC(2026, 0, 16, 17, 0) / 1000);
  });
});

describe("canSubmit", () => {
  it("accepts a complete form with no template problems", () => {
    expect(canSubmit(filled(), [])).toBe(true);
  });
  it("is false while the template has problems", () => {
    expect(canSubmit(filled(), ["bot role is below mod"])).toBe(false);
  });
  it.each([
    ["hub", { hubId: null }],
    ["role", { roleId: null }],
    ["name", { huntName: "   " }],
    ["url", { huntUrl: "" }],
  ] as const)("is false when the %s is missing", (_n, over) => {
    expect(canSubmit(filled(over), [])).toBe(false);
  });
});

describe("buildPayload multi_team", () => {
  it("sends the hub category (trimmed, blank allowed) only with multi_team", () => {
    expect(buildPayload(filled({ multiTeam: true })).hub_category).toBe("general");
    expect(buildPayload(filled({ multiTeam: true, hubCategory: "  Hunts " })).hub_category)
      .toBe("Hunts");
    expect(buildPayload(filled({ multiTeam: true, hubCategory: " " })).hub_category).toBe("");
    expect("hub_category" in buildPayload(filled({ hubCategory: "Hunts" }))).toBe(false);
  });

  it("sends multi_team: true only when set", () => {
    expect(buildPayload(filled({ multiTeam: true }))).toMatchObject({ multi_team: true });
    expect("multi_team" in buildPayload(filled({ multiTeam: false }))).toBe(false);
    expect("multi_team" in buildPayload(filled())).toBe(false);
  });
});

describe("multiTeamMatch", () => {
  const running = [
    { id: 1, name: "Puzzle Boat", team_count: 2, url: "", start_timestamp: null },
    { id: 2, name: "Mystery Hunt 2027", team_count: 3, url: "", start_timestamp: null },
  ];

  it("matches exactly", () => {
    expect(multiTeamMatch("Mystery Hunt 2027", running)).toEqual(running[1]);
  });
  it("ignores case", () => {
    expect(multiTeamMatch("mystery HUNT 2027", running)).toEqual(running[1]);
  });
  it("ignores surrounding spaces", () => {
    expect(multiTeamMatch("  Puzzle Boat  ", running)).toEqual(running[0]);
  });
  it("returns null when nothing matches (partial is not a match)", () => {
    expect(multiTeamMatch("Mystery Hunt", running)).toBeNull();
    expect(multiTeamMatch("", running)).toBeNull();
  });
  it("returns null for an empty list", () => {
    expect(multiTeamMatch("Puzzle Boat", [])).toBeNull();
  });
});

describe("pickRunningHunt", () => {
  const gph = { id: 3, name: "GPH 2026", team_count: 2, url: "https://gph.test",
                start_timestamp: Date.UTC(2026, 3, 10, 17, 0) / 1000 };

  it("fills the name, website and start (in the form's offset), not the login", () => {
    const form = filled({ huntName: "", huntUrl: "", startOffsetMinutes: 60,
                          username: "me", password: "pw" });
    const next = pickRunningHunt(form, gph);
    expect(next.huntName).toBe("GPH 2026");
    expect(next.huntUrl).toBe("https://gph.test");
    expect(next.startLocal).toBe("2026-04-10T18:00");     // 17:00 UTC in UTC+1
    expect(next.startOffsetMinutes).toBe(60);
    expect([next.username, next.password]).toEqual(["me", "pw"]);
    expect(buildPayload(next).start_timestamp).toBe(gph.start_timestamp);
  });

  it("keeps what was typed when the running hunt has no website or start", () => {
    const form = filled({ huntUrl: "https://typed.test", startLocal: "2026-01-01T09:00" });
    const next = pickRunningHunt(form, { ...gph, url: "", start_timestamp: null });
    expect(next.huntName).toBe("GPH 2026");
    expect(next.huntUrl).toBe("https://typed.test");
    expect(next.startLocal).toBe("2026-01-01T09:00");
  });
});
