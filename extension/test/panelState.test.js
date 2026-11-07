import { describe, it, expect } from "vitest";
import { cacheValid, stateFor, eventChannel, applyEvent } from "../src/lib/panelState.js";

const e = (extra = {}) => ({ id: 1, spreadsheetId: "s", gid: 5, channel_id: "9", bridge_enabled: true, ...extra });
const m = (id, content = "x") => ({ id, content });

describe("stateFor", () => {
  it("picks the state", () => {
    expect(stateFor(e())).toBe("chat");
    expect(stateFor(e({ bridge_enabled: false }))).toBe("unavailable");
    expect(stateFor(e({ channel_id: null }))).toBe("no_channel");
    expect(stateFor(undefined, "auth")).toBe("auth");
    expect(stateFor(undefined, "error")).toBe("error");
    expect(stateFor(undefined, "notfound")).toBe("not_puzzle");
    expect(stateFor(undefined)).toBe("not_puzzle");
  });
});

describe("cacheValid", () => {
  it("requires same spreadsheet and gid", () => {
    expect(cacheValid(e(), { spreadsheetId: "s", gid: 5 })).toBe(true);
    expect(cacheValid(e(), { spreadsheetId: "s", gid: 6 })).toBe(false);
    expect(cacheValid(e(), { spreadsheetId: "t", gid: 5 })).toBe(false);
    expect(cacheValid(e({ gid: null }), { spreadsheetId: "s", gid: null })).toBe(true);
    expect(cacheValid(undefined, { spreadsheetId: "s", gid: 5 })).toBe(false);
  });
});

describe("eventChannel", () => {
  it("returns channel for message events only", () => {
    expect(eventChannel({ type: "message", channel_id: "9" })).toBe("9");
    expect(eventChannel({ type: "message_delete", channel_id: "8" })).toBe("8");
    expect(eventChannel({ type: "message_edit", channel_id: null })).toBeNull();
    expect(eventChannel({ type: "ready" })).toBeNull();
    expect(eventChannel(null)).toBeNull();
  });
});

describe("applyEvent", () => {
  it("appends, dedupes, edits and deletes by id", () => {
    let l = [m("1"), m("2")];
    l = applyEvent(l, { type: "message", message: m("3") });
    expect(l.map((x) => x.id)).toEqual(["1", "2", "3"]);
    expect(applyEvent(l, { type: "message", message: m("3") })).toBe(l);
    l = applyEvent(l, { type: "message_edit", message: { id: "2", content: "new" } });
    expect(l.map((x) => x.content)).toEqual(["x", "new", "x"]);
    l = applyEvent(l, { type: "message_delete", message_id: "1" });
    expect(l.map((x) => x.id)).toEqual(["2", "3"]);
  });
});
