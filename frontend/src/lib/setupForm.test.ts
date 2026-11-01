import { describe, expect, it } from "vitest";
import { buildPayload, canSubmit, emptySetupForm, type SetupForm } from "./setupForm";

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
