import { describe, it, expect } from "vitest";
import { attachmentView } from "../src/lib/attachments.js";

const O = "https://dash.example";

describe("attachmentView", () => {
  it("links a downloaded attachment to origin + url", () => {
    expect(attachmentView({ filename: "a.png", url: "/api/attachments/12" }, O)).toEqual({
      text: "a.png",
      href: "https://dash.example/api/attachments/12",
    });
  });
  it("has no href when not downloaded", () => {
    expect(attachmentView({ filename: "a.png", url: null }, O)).toEqual({ text: "a.png", href: null });
  });
  it("has no href for a url outside /api/attachments/", () => {
    expect(attachmentView({ filename: "x", url: "https://evil.example/a" }, O).href).toBeNull();
    expect(attachmentView({ filename: "x", url: "//evil.example/api/attachments/1" }, O).href).toBeNull();
    expect(attachmentView({ filename: "x", url: "/api/other/1" }, O).href).toBeNull();
  });
  it("falls back to a generic name", () => {
    expect(attachmentView({ url: null }, O).text).toBe("attachment");
  });
});
