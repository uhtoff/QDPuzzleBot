// Pure helpers for build-guide.mjs: split the user guide's markdown into one page per
// `##` section, keep heading ids exactly as the old single page had them, and build the
// search index. Tested in guide-lib.test.mjs.

// GitHub's heading slugs, which the guide's in-page #anchors were written against.
export const slug = (text) =>
  text
    .toLowerCase()
    .replace(/[`*_~]/g, "")
    .replace(/[^\w\- ]+/g, "")
    .trim()
    .replace(/ /g, "-");

// "1. The basics — a puzzle's life" -> "the-basics": the page's file name.
export const sectionSlug = (heading) => slug(heading.replace(/^\d+\.\s*/, "").split(" — ")[0]);

// Heading token -> id, numbered across the WHOLE document (-1, -2 for repeats, as GitHub
// does), so ids stay what the single page used and old #links still resolve.
export function headingIds(tokens) {
  const ids = new Map();
  const seen = new Map();
  for (const t of tokens) {
    if (t.type !== "heading") continue;
    const base = slug(t.text);
    const n = seen.get(base) ?? 0;
    seen.set(base, n + 1);
    ids.set(t, n === 0 ? base : `${base}-${n}`);
  }
  return ids;
}

// The intro (before the first `##`) becomes the overview page; each `##` starts a page.
export function splitPages(tokens, ids) {
  const page = (slugName, title) => {
    const toks = [];
    toks.links = tokens.links; // reference-style links resolve on every page
    return { slug: slugName, title, tokens: toks, headings: [], ids: [] };
  };
  const pages = [page("index", "Overview")];
  for (const t of tokens) {
    if (t.type === "heading" && t.depth === 2) pages.push(page(sectionSlug(t.text), t.text));
    const current = pages[pages.length - 1];
    current.tokens.push(t);
    if (t.type === "heading") {
      current.ids.push(ids.get(t));
      if (t.depth === 3) current.headings.push({ id: ids.get(t), text: t.text });
    }
  }
  return pages;
}

// Same-page anchors stay; one whose heading lives on another page points there.
export function rewriteAnchors(html, pageSlug, pageOf) {
  return html.replace(/href="#([^"]+)"/g, (whole, id) => {
    const target = pageOf.get(id);
    return target && target !== pageSlug ? `href="${target}.html#${id}"` : whole;
  });
}

// Readable text of a token list: link text without its URL, image alt text, code as-is.
function plain(tokens) {
  const out = [];
  for (const t of tokens ?? []) {
    if (t.type === "heading" || t.type === "html" || t.type === "space" || t.type === "hr") continue;
    if (t.type === "list") for (const item of t.items) out.push(plain(item.tokens));
    else if (t.type === "table") {
      for (const cell of t.header) out.push(plain(cell.tokens));
      for (const row of t.rows) for (const cell of row) out.push(plain(cell.tokens));
    } else if (Array.isArray(t.tokens)) out.push(plain(t.tokens));
    else if (typeof t.text === "string") out.push(t.text);
  }
  return out.join(" ").replace(/\s+/g, " ").trim();
}

// One entry per `###` (found by its title even when it has no text), plus the section's own
// text before its first `###` when there is any; one entry for a page with no `###`.
export function searchEntries(pages) {
  const entries = [];
  for (const p of pages) {
    let sub = 0; // index into p.headings, the page's ### headings in order
    let chunk = { page: p.slug, id: p.ids[0] ?? null, title: p.title, section: p.title, tokens: [] };
    const flush = (always) => {
      const text = plain(chunk.tokens);
      if (text || always) entries.push({ page: chunk.page, id: chunk.id, title: chunk.title, section: chunk.section, text });
    };
    for (const t of p.tokens) {
      if (t.type === "heading" && t.depth === 3) {
        flush(chunk.sub);
        chunk = { page: p.slug, id: p.headings[sub++]?.id ?? null, title: t.text, section: p.title, tokens: [], sub: true };
        continue;
      }
      chunk.tokens.push(t);
    }
    flush(chunk.sub || p.headings.length === 0);
  }
  return entries;
}
