import { describe, it, expect } from "vitest";
import { rewriteMentions, tokenise, notificationText, stripMarkers, embedLines, safeLink } from "../src/lib/discordText.js";

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

describe("tokenise links", () => {
  it("a bare link, with the sentence's punctuation left outside it", () => {
    expect(tokenise("see https://a.example/x?y=1. ok")).toEqual([
      { text: "see " }, { text: "https://a.example/x?y=1", href: "https://a.example/x?y=1" }, { text: ". ok" },
    ]);
  });
  it("two links keep their order and text between", () => {
    expect(tokenise("http://one.example and https://two.example")).toEqual([
      { text: "http://one.example", href: "http://one.example" },
      { text: " and " },
      { text: "https://two.example", href: "https://two.example" },
    ]);
  });
  it("<url> (no preview) drops the brackets", () => {
    expect(tokenise("go <https://a.example/p> now")).toEqual([
      { text: "go " }, { text: "https://a.example/p", href: "https://a.example/p" }, { text: " now" },
    ]);
  });
  it("[text](url) shows the text and links the url", () => {
    expect(tokenise("[the sheet](https://a.example/s) here")).toEqual([
      { text: "the sheet", href: "https://a.example/s" }, { text: " here" },
    ]);
  });
  it("a link inside code stays code", () => {
    expect(tokenise("`https://a.example`")).toEqual([{ text: "https://a.example", code: true }]);
  });
  it("only http(s) is linked", () => {
    expect(tokenise("javascript:alert(1) [x](javascript:alert(1)) ftp://a.example")).toEqual([
      { text: "javascript:alert(1) [x](javascript:alert(1)) ftp://a.example" },
    ]);
  });
  it("shortcodes aren't replaced inside a link", () => {
    expect(tokenise("https://a.example/:smile:/x")).toEqual([
      { text: "https://a.example/:smile:/x", href: "https://a.example/:smile:/x" },
    ]);
  });
});

describe("safeLink", () => {
  it("keeps http(s) urls", () => expect(safeLink("https://a.example/x")).toBe("https://a.example/x"));
  it("refuses anything else", () => {
    for (const u of ["javascript:alert(1)", "data:text/html,x", "not a url", "", null]) expect(safeLink(u)).toBe(null);
  });
});

describe("embedLines", () => {
  it("links the title when the embed has an http(s) url", () => {
    expect(embedLines({ title: "T", url: "https://a.example", description: "d" })).toEqual([
      { kind: "title", text: "T", href: "https://a.example" }, { kind: "description", text: "d" },
    ]);
  });
  it("leaves the title plain for any other url", () => {
    expect(embedLines({ title: "T", url: "javascript:x" })).toEqual([{ kind: "title", text: "T" }]);
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
  it("shows links as text: <url> unwrapped, [text](url) as its text", () => {
    expect(notificationText({ author_name: "B", content: "<https://a.example> and [sheet](https://b.example)" })).toBe(
      "B: https://a.example and sheet",
    );
  });
});
