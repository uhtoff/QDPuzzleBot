// Notes (puzzle notes, canvas region notes) are typed by any team member and
// also read in Discord, so the dashboard renders the markdown Discord does --
// as data for React elements, never as an HTML string.
import { describe, expect, it } from "vitest";
import { emojify } from "../emoji";
import { hasFormatting, parseBlocks, parseInline, type Inline } from "./discordMarkdown";

const t = (text: string): Inline => ({ kind: "text", text });

describe("parseInline", () => {
  it("plain text is one text span, with emoji shortcodes replaced", () => {
    expect(parseInline("just words")).toEqual([t("just words")]);
    expect(parseInline("done :tada:")).toEqual([t(emojify("done :tada:"))]);
  });

  it("bold, italic, underline and strikethrough", () => {
    expect(parseInline("a **b** c")).toEqual([t("a "), { kind: "bold", children: [t("b")] }, t(" c")]);
    expect(parseInline("*i*")).toEqual([{ kind: "italic", children: [t("i")] }]);
    expect(parseInline("_i_")).toEqual([{ kind: "italic", children: [t("i")] }]);
    expect(parseInline("__u__")).toEqual([{ kind: "underline", children: [t("u")] }]);
    expect(parseInline("~~s~~")).toEqual([{ kind: "strike", children: [t("s")] }]);
  });

  it("***x*** is bold and italic", () => {
    expect(parseInline("***x***")).toEqual([{ kind: "bold", children: [{ kind: "italic", children: [t("x")] }] }]);
  });

  it("markers nest", () => {
    expect(parseInline("**bold `code` and *it***")).toEqual([
      { kind: "bold", children: [t("bold "), { kind: "code", text: "code" }, t(" and "), { kind: "italic", children: [t("it")] }] },
    ]);
  });

  it("code is literal: no markers, no emoji", () => {
    expect(parseInline("`**x** :tada:`")).toEqual([{ kind: "code", text: "**x** :tada:" }]);
  });

  it("an unmatched marker stays as typed", () => {
    expect(parseInline("a ** b")).toEqual([t("a ** b")]);
    expect(parseInline("~~open")).toEqual([t("~~open")]);
  });

  it("an underscore inside a word is not italic (snake_case)", () => {
    expect(parseInline("snake_case_word")).toEqual([t("snake_case_word")]);
  });

  it("an asterisk next to a space is not italic (2 * 3 * 4)", () => {
    expect(parseInline("2 * 3 * 4")).toEqual([t("2 * 3 * 4")]);
  });

  it("[text](url) is a link with its text parsed", () => {
    expect(parseInline("see [the **map**](https://ex.com/m)")).toEqual([
      t("see "),
      { kind: "link", href: "https://ex.com/m", children: [t("the "), { kind: "bold", children: [t("map")] }] },
    ]);
  });

  it("a link to anything but http(s) stays as typed", () => {
    expect(parseInline("[x](javascript:alert(1))")).toEqual([t("[x](javascript:alert(1))")]);
  });

  it("a bare URL is a link, without trailing punctuation", () => {
    expect(parseInline("go to https://ex.com/a?b=1.")).toEqual([
      t("go to "),
      { kind: "link", href: "https://ex.com/a?b=1", children: [t("https://ex.com/a?b=1")] },
      t("."),
    ]);
  });

  it("underscores inside a bare URL are not italic", () => {
    expect(parseInline("https://ex.com/a_b_c")).toEqual([
      { kind: "link", href: "https://ex.com/a_b_c", children: [t("https://ex.com/a_b_c")] },
    ]);
  });
});

describe("parseBlocks", () => {
  it("lines run together into a paragraph; a blank line starts another", () => {
    expect(parseBlocks("a\nb\n\nc")).toEqual([
      { kind: "paragraph", lines: [[t("a")], [t("b")]] },
      { kind: "paragraph", lines: [[t("c")]] },
    ]);
  });

  it("headings # to ###; #### is not one", () => {
    expect(parseBlocks("# One\n### Three\n#### Four")).toEqual([
      { kind: "heading", level: 1, content: [t("One")] },
      { kind: "heading", level: 3, content: [t("Three")] },
      { kind: "paragraph", lines: [[t("#### Four")]] },
    ]);
  });

  it("-# is small text", () => {
    expect(parseBlocks("-# aside")).toEqual([{ kind: "subtext", content: [t("aside")] }]);
  });

  it("> lines are one quote", () => {
    expect(parseBlocks("> one\n> **two**\nafter")).toEqual([
      { kind: "quote", lines: [[t("one")], [{ kind: "bold", children: [t("two")] }]] },
      { kind: "paragraph", lines: [[t("after")]] },
    ]);
  });

  it("- and * lists; numbered lists keep their first number", () => {
    expect(parseBlocks("- a\n* b\n\n3. c\n4. d")).toEqual([
      { kind: "list", ordered: false, start: 1, items: [[t("a")], [t("b")]] },
      { kind: "list", ordered: true, start: 3, items: [[t("c")], [t("d")]] },
    ]);
  });

  it("a fenced code block is literal, language line dropped", () => {
    expect(parseBlocks("before\n```py\nx = **1**\n\ny\n```\nafter")).toEqual([
      { kind: "paragraph", lines: [[t("before")]] },
      { kind: "codeblock", text: "x = **1**\n\ny" },
      { kind: "paragraph", lines: [[t("after")]] },
    ]);
  });

  it("an unclosed fence is ordinary text", () => {
    expect(parseBlocks("```\nx")).toEqual([{ kind: "paragraph", lines: [[t("```")], [t("x")]] }]);
  });

  it("empty text is no blocks", () => {
    expect(parseBlocks("")).toEqual([]);
    expect(parseBlocks("\n\n")).toEqual([]);
  });
});

describe("hasFormatting", () => {
  it("plain text, even over several lines, has none", () => {
    expect(hasFormatting("just a note\nsecond line")).toBe(false);
    expect(hasFormatting("2 * 3 = 6, snake_case")).toBe(false);
  });
  it("any marker, link, heading, quote, list or code block has some", () => {
    for (const s of ["a **b**", "see https://ex.com", "# h", "> q", "- a", "```\nx\n```", "`c`"]) {
      expect(hasFormatting(s)).toBe(true);
    }
  });
});
