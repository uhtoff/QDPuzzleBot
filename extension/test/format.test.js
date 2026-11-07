import { describe, it, expect } from "vitest";
import { messageView, isBangCommand } from "../src/lib/format.js";

describe("isBangCommand", () => {
  it("is true for a leading bang, ignoring whitespace", () => {
    expect(isBangCommand("  !s X")).toBe(true);
    expect(isBangCommand("!s")).toBe(true);
  });
  it("is false otherwise", () => {
    expect(isBangCommand("hi !s")).toBe(false);
    expect(isBangCommand("")).toBe(false);
  });
});

describe("messageView", () => {
  it("maps the server message fields", () => {
    expect(messageView({ id: "1", author_name: "Ann", author_avatar: "http://a/x.png", content: "hello", created_at: "2026-01-02T03:04:05Z", kind: "default" }))
      .toEqual({ author: "Ann", avatar: "http://a/x.png", text: "hello", time: "2026-01-02T03:04:05Z", hasEmbeds: false });
  });
  it("flags embeds only when the embeds list is non-empty", () => {
    expect(messageView({ content: "", embeds: [{ title: "x" }] }).hasEmbeds).toBe(true);
    expect(messageView({ content: "", embeds: [] }).hasEmbeds).toBe(false);
    expect(messageView({ content: "" }).hasEmbeds).toBe(false);
  });
  it("tolerates missing avatar and content", () => {
    expect(messageView({ author_name: "Bob", created_at: "t" })).toEqual({ author: "Bob", avatar: null, text: "", time: "t", hasEmbeds: false });
  });
});
