import { describe, it, expect } from "vitest";
import { rewriteMentions, tokenise, notificationText, stripMarkers } from "../src/lib/discordText.js";

const names = { 111: "Cipher" };
const lookup = (id) => names[id];

describe("rewriteMentions", () => {
  it("channel known vs unknown", () => {
    expect(rewriteMentions("see <#111> and <#222>", lookup)).toBe("see #Cipher and #channel");
  });
  it("no lookup falls back to #channel", () => {
    expect(rewriteMentions("<#111>")).toBe("#channel");
  });
  it("users, roles, emoji", () => {
    expect(rewriteMentions("<@1> <@!2> <@&3> <:wave:9> <a:dance:8>")).toBe("@someone @someone @role :wave: :dance:");
  });
});

describe("tokenise", () => {
  it("bold, code and plain", () => {
    expect(tokenise("a **b** `c` d")).toEqual([
      { text: "a " }, { text: "b", bold: true }, { text: " " }, { text: "c", code: true }, { text: " d" },
    ]);
  });
  it("adjacent spans", () => {
    expect(tokenise("**a**`b`")).toEqual([{ text: "a", bold: true }, { text: "b", code: true }]);
  });
  it("unmatched markers stay literal", () => {
    expect(tokenise("**a `b")).toEqual([{ text: "**a `b" }]);
  });
  it("output is plain data, HTML stays literal text", () => {
    const out = tokenise("<b>x</b> **y**");
    expect(out).toEqual([{ text: "<b>x</b> " }, { text: "y", bold: true }]);
    expect(out.every((s) => Object.keys(s).every((k) => ["text", "bold", "code"].includes(k)))).toBe(true);
  });
});

describe("notificationText", () => {
  it("rewrites, strips markers, prefixes author", () => {
    expect(notificationText({ author_name: "Bob", content: "**hi** <#111> `x`" }, lookup)).toBe("Bob: hi #Cipher x");
  });
  it("truncates after cleaning", () => {
    const t = notificationText({ author_name: "B", content: "**" + "a".repeat(200) + "**" });
    expect(t.length).toBe(120);
    expect(t.endsWith("…")).toBe(true);
  });
  it("embed-only uses title and description", () => {
    expect(notificationText({ author_name: "Bot", content: "", embeds: [{ title: "PUZZLE SOLVED!", description: "**X** in <#111>" }] }, lookup)).toBe(
      "Bot: PUZZLE SOLVED! X in #Cipher",
    );
  });
  it("stripMarkers", () => expect(stripMarkers("**a**`b`")).toBe("ab"));
});
