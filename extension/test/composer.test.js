import { describe, it, expect } from "vitest";
import { canCompose, validate, draftAfterSend } from "../src/lib/composer.js";
import { errorText } from "../src/lib/format.js";

describe("errorText", () => {
  it("maps every known code to a distinct specific message", () => {
    const codes = ["empty", "too_long", "command", "archived", "forbidden", "not_found_or_forbidden", "send_failed", "csrf"];
    const texts = codes.map(errorText);
    expect(new Set(texts).size).toBe(codes.length);
    expect(errorText("command")).toMatch(/Discord/);
    expect(errorText("too_long")).toMatch(/2000/);
    expect(errorText("csrf")).toMatch(/reload/i);
    expect(errorText("send_failed")).toMatch(/try again/i);
  });
  it("falls back generically without echoing the code", () => {
    const fb = errorText("<b>weird</b>");
    expect(fb).toBe("Could not send the message.");
    expect(errorText("toString")).toBe(fb);
    expect(errorText(undefined)).toBe(fb);
    expect(errorText("weird")).not.toContain("weird");
  });
});

describe("validate", () => {
  it("trims and accepts normal text", () => {
    expect(validate("  hi there \n")).toEqual({ ok: true, content: "hi there" });
  });
  it("rejects empty and whitespace", () => {
    expect(validate("")).toEqual({ ok: false, code: "empty" });
    expect(validate("  \n ")).toEqual({ ok: false, code: "empty" });
  });
  it("rejects bang commands, even after whitespace", () => {
    expect(validate("!s X")).toEqual({ ok: false, code: "command" });
    expect(validate("  !s X")).toEqual({ ok: false, code: "command" });
  });
  it("enforces the 2000 limit on trimmed content", () => {
    expect(validate("a".repeat(2000)).ok).toBe(true);
    expect(validate("a".repeat(2001))).toEqual({ ok: false, code: "too_long" });
    expect(validate(" " + "a".repeat(2000) + " ").ok).toBe(true);
  });
});

describe("canCompose", () => {
  const p = { channel_id: "5", hunt_archived: false };
  it("allows a live chat puzzle", () => expect(canCompose("chat", p)).toBe(true));
  it("blocks other states, missing channel, archived hunts", () => {
    expect(canCompose("unavailable", p)).toBe(false);
    expect(canCompose("not_puzzle", undefined)).toBe(false);
    expect(canCompose("chat", { ...p, channel_id: null })).toBe(false);
    expect(canCompose("chat", { ...p, hunt_archived: true })).toBe(false);
    expect(canCompose("chat", undefined)).toBe(false);
  });
});

describe("draftAfterSend", () => {
  it("clears only an unchanged draft", () => {
    expect(draftAfterSend("hello", "hello")).toBe("");
    expect(draftAfterSend("hello world", "hello")).toBe("hello world");
  });
});
