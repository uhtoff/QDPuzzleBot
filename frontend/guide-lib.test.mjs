import { describe, expect, it } from "vitest";
import { marked } from "marked";
import { headingIds, rewriteAnchors, searchEntries, sectionSlug, slug, splitPages } from "./guide-lib.mjs";

const DOC = `# Guide

Intro text.

## 1. The basics — a puzzle's life

### Where things live

Basics body, see [Canvas](#canvas).

### Solving

## 2. The web dashboard

### Canvas

Canvas body with **bold** and [a link](https://x.test).

### Solving

Dashboard solving.
`;

describe("slug and sectionSlug", () => {
  it("follows GitHub's heading slugs", () => {
    expect(slug('"All puzzles" — search and filter')).toBe("all-puzzles--search-and-filter");
    expect(slug("Reading a puzzle's chat")).toBe("reading-a-puzzles-chat");
  });
  it("names a section page from its heading without the number or the dash clause", () => {
    expect(sectionSlug("1. The basics — a puzzle's life")).toBe("the-basics");
    expect(sectionSlug("6. The web dashboard")).toBe("the-web-dashboard");
    expect(sectionSlug("7. For organisers and admins")).toBe("for-organisers-and-admins");
  });
});

describe("headingIds", () => {
  it("numbers repeated headings across the whole document, as the single page did", () => {
    const ids = [...headingIds(marked.lexer(DOC)).values()];
    expect(ids).toEqual(["guide", "1-the-basics--a-puzzles-life", "where-things-live", "solving", "2-the-web-dashboard", "canvas", "solving-1"]);
  });
});

describe("splitPages", () => {
  const tokens = marked.lexer(DOC);
  const ids = headingIds(tokens);
  const pages = splitPages(tokens, ids);
  it("makes an overview from the intro, then one page per ## section in order", () => {
    expect(pages.map((p) => p.slug)).toEqual(["index", "the-basics", "the-web-dashboard"]);
    expect(pages[1].title).toBe("1. The basics — a puzzle's life");
    expect(pages[0].tokens.some((t) => t.type === "heading" && t.depth === 2)).toBe(false);
  });
  it("lists each page's ### headings with their document-wide ids", () => {
    expect(pages[2].headings).toEqual([
      { id: "canvas", text: "Canvas" },
      { id: "solving-1", text: "Solving" },
    ]);
  });
  it("maps every heading id to the page it lives on", () => {
    const pageOf = new Map(pages.flatMap((p) => p.ids.map((id) => [id, p.slug])));
    expect(pageOf.get("canvas")).toBe("the-web-dashboard");
    expect(pageOf.get("solving")).toBe("the-basics");
    expect(pageOf.get("1-the-basics--a-puzzles-life")).toBe("the-basics");
  });
});

describe("rewriteAnchors", () => {
  const pageOf = new Map([["canvas", "the-web-dashboard"], ["where-things-live", "the-basics"]]);
  it("points an in-guide link at the page holding its heading, leaving same-page and unknown ones", () => {
    const html = '<a href="#canvas">C</a> <a href="#where-things-live">W</a> <a href="#nope">N</a>';
    expect(rewriteAnchors(html, "the-basics", pageOf)).toBe(
      '<a href="the-web-dashboard.html#canvas">C</a> <a href="#where-things-live">W</a> <a href="#nope">N</a>',
    );
  });
});

describe("searchEntries", () => {
  it("has one entry per ### (or per page without any), in plain text", () => {
    const tokens = marked.lexer(DOC);
    const pages = splitPages(tokens, headingIds(tokens));
    const entries = searchEntries(pages);
    const canvas = entries.find((e) => e.id === "canvas");
    expect(canvas).toMatchObject({ page: "the-web-dashboard", title: "Canvas", section: "2. The web dashboard" });
    expect(canvas.text).toContain("Canvas body with bold and a link");
    expect(canvas.text).not.toContain("**");
    expect(canvas.text).not.toContain("https://x.test");
    expect(entries.find((e) => e.page === "index")).toMatchObject({ title: "Overview" });
    expect(entries.filter((e) => e.page === "the-basics").map((e) => e.id)).toEqual(["where-things-live", "solving"]);
  });
});
