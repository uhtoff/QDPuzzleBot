// Generate the static docs served by the dashboard: the user guide as a small docs site
// under /guide/ (one page per `##` section, a sidebar, previous/next links and search),
// /guide.html as a stub that forwards old links there, and /commands.html.
//
// Runs as npm's prebuild hook, so every `npm run build` (including the one in
// start.sh on deploy) re-renders the markdown into frontend/public/, which Vite
// copies verbatim into dist. Output is gitignored — the markdown is the source
// of truth. Pure helpers (splitting, ids, search index) live in guide-lib.mjs.
import { copyFileSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { marked } from "marked";
import { headingIds, rewriteAnchors, searchEntries, splitPages } from "./guide-lib.mjs";

const frontendDir = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(frontendDir, "..");
const srcImgDir = join(repoRoot, "docs", "img", "userguide");
const outDir = join(frontendDir, "public");
const guideDir = join(outDir, "guide");
const outImgDir = join(guideDir, "img");
const GUIDE_TITLE = "Puzzbot User Guide";

// Relative *.md links work in the repo but 404 on the dashboard. Point links at a
// published doc to its page (`resolve(target, anchor)` gives the URL, or null), and unwrap
// the rest to plain text so no dead link ships. Absolute, external and anchor-only links
// are left alone.
const MD_LINK = /\[([^\]]+)\]\((?!https?:|\/|#)([^)\s]+?\.md)(#[^)\s]*)?\)/g;
const rewriteLinks = (md, resolveLink) =>
  md.replace(MD_LINK, (whole, text, target, anchor = "") => {
    const url = resolveLink(target.replace(/^(\.\/|\.\.\/)+/, ""), anchor.slice(1));
    return url ? `[${text}](${url})` : text;
  });

// Heading ids come from guide-lib's headingIds (GitHub slugs, numbered over the whole
// document), looked up by token while rendering.
let idOf = new Map();
marked.use({
  renderer: {
    heading(token) {
      const id = idOf.get(token);
      const attr = id ? ` id="${id}"` : "";
      return `<h${token.depth}${attr}>${this.parser.parseInline(token.tokens)}</h${token.depth}>\n`;
    },
  },
});

const escapeHtml = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const inline = (md) => marked.parseInline(md);
const plainTitle = (md) => md.replace(/[`*_]/g, "");

const baseCss = `
  h1 { font-size: 1.9em; border-bottom: 3px solid #2f9e44; padding-bottom: 6px; }
  h2 { font-size: 1.35em; color: #1864ab; margin-top: 1.8em;
       border-bottom: 1px solid #dee2e6; padding-bottom: 4px; }
  h3 { font-size: 1.1em; margin-top: 1.4em; }
  code { font-family: ui-monospace, 'Cascadia Code', Menlo, monospace;
         font-size: 0.85em; background: #f1f3f5; padding: 1px 5px; border-radius: 4px; }
  pre { background: #f1f3f5; padding: 10px 14px; border-radius: 8px;
        overflow-x: auto; }
  pre code { background: none; padding: 0; }
  table { border-collapse: collapse; width: 100%; margin: 1em 0; font-size: 0.92em; }
  th, td { border: 1px solid #ced4da; padding: 6px 10px; text-align: left; }
  th { background: #e7f5ff; }
  img { max-width: 100%; border: 1px solid #dee2e6; border-radius: 8px; margin: 0.6em 0; }
  blockquote { border-left: 4px solid #74c0fc; margin-left: 0; padding-left: 14px;
               color: #495057; }
  hr { border: none; border-top: 1px solid #dee2e6; margin: 2em 0; }
  a { color: #1864ab; }
  .back { font-size: 0.9em; }
`;
const font = `font-family: system-ui, -apple-system, 'Segoe UI', Arial, sans-serif;
         font-size: 16px; line-height: 1.55; color: #1a1a2e;`;

// The single-page layout (commands.html), as before.
const singleCss = `body { ${font} max-width: 880px; margin: 0 auto; padding: 24px 20px 80px; }${baseCss}`;

// The guide: sidebar beside the page; on narrow screens a top bar with a Menu button.
const guideCss = `body { ${font} margin: 0; background: #fff; }${baseCss}
  .layout { display: grid; grid-template-columns: 270px minmax(0, 1fr); gap: 44px;
            max-width: 1180px; margin: 0 auto; padding: 0 20px; }
  .side { position: sticky; top: 0; align-self: start; max-height: 100vh; overflow-y: auto;
          padding: 24px 4px 40px 0; font-size: 0.93em; }
  main { padding: 20px 0 80px; max-width: 820px; min-width: 0; }
  main > h2:first-child { font-size: 1.75em; margin-top: 0.3em; }
  .brand { display: block; font-weight: 700; color: #1a1a2e; text-decoration: none;
           margin-bottom: 12px; font-size: 1.05em; }
  .search input { width: 100%; box-sizing: border-box; padding: 7px 10px; font: inherit;
                  border: 1px solid #ced4da; border-radius: 8px; }
  #results { list-style: none; padding: 0; margin: 6px 0 10px; }
  #results a { display: block; padding: 6px 8px; border-radius: 6px; text-decoration: none;
               color: #1a1a2e; }
  #results a:hover, #results a:focus { background: #f1f3f5; }
  #results b { display: block; }
  #results span { display: block; font-size: 0.8em; color: #1864ab; }
  #results small { display: block; color: #495057; }
  #results .none { color: #868e96; padding: 6px 8px; }
  .toc { list-style: none; padding: 0; margin: 10px 0; }
  .toc a { display: block; padding: 4px 8px; border-radius: 6px; text-decoration: none;
           color: #1a1a2e; }
  .toc a:hover { background: #f1f3f5; }
  .toc a.current { background: #e7f5ff; color: #1864ab; font-weight: 600; }
  .toc ul { list-style: none; padding-left: 10px; margin: 2px 0 8px 8px;
            border-left: 2px solid #e7f5ff; }
  .toc ul a { font-size: 0.95em; color: #495057; padding: 3px 8px; }
  .more { border-top: 1px solid #dee2e6; padding-top: 10px; line-height: 1.9; }
  .more a { text-decoration: none; }
  .pager { display: flex; justify-content: space-between; gap: 12px; margin-top: 3em;
           padding-top: 1em; border-top: 1px solid #dee2e6; }
  .pager a { text-decoration: none; max-width: 48%; }
  .pager .next { margin-left: auto; text-align: right; }
  .pager small { display: block; color: #868e96; }
  .contents li { margin: 3px 0; }
  .topbar { display: none; }
  @media (max-width: 820px) {
    .layout { grid-template-columns: 1fr; padding: 0 16px; }
    .topbar { display: flex; position: sticky; top: 0; z-index: 2; gap: 12px;
              align-items: center; background: #fff; border-bottom: 1px solid #dee2e6;
              padding: 8px 16px; }
    .topbar a { font-weight: 600; text-decoration: none; color: #1a1a2e; }
    .topbar button { font: inherit; padding: 5px 10px; border: 1px solid #ced4da;
                     border-radius: 8px; background: #fff; }
    .side { display: none; position: fixed; inset: 49px 0 0 0; z-index: 1; background: #fff;
            max-height: none; align-self: stretch; overflow-y: auto; padding: 16px; }
    body.nav-open { overflow: hidden; }
    body.nav-open .side { display: block; }
    .brand { display: none; }
  }
`;

const singlePage = (title, body) => `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${title}</title>
<style>${singleCss}</style>
</head>
<body>
<p class="back"><a href="/">&larr; Back to the dashboard</a></p>
${body}
</body>
</html>
`;

// Search and the phone menu. The index is fetched on first use.
const guideScript = `
(() => {
  const body = document.body;
  document.getElementById("menu").addEventListener("click", () => body.classList.toggle("nav-open"));
  document.getElementById("side").addEventListener("click", (e) => {
    if (e.target.closest("a")) body.classList.remove("nav-open");
  });
  const q = document.getElementById("q");
  const results = document.getElementById("results");
  let index = null;
  const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  q.addEventListener("input", async () => {
    const words = q.value.toLowerCase().split(/\\s+/).filter(Boolean);
    if (!words.length) { results.innerHTML = ""; return; }
    if (!index) {
      try { index = await (await fetch("search-index.json")).json(); }
      catch { results.innerHTML = '<li class="none">Search is unavailable</li>'; return; }
    }
    const hits = [];
    for (const e of index) {
      const title = e.title.toLowerCase();
      const text = e.text.toLowerCase();
      if (!words.every((w) => title.includes(w) || text.includes(w))) continue;
      hits.push([words.filter((w) => title.includes(w)).length, e]);
    }
    hits.sort((a, b) => b[0] - a[0]);
    results.innerHTML = hits.slice(0, 12).map(([, e]) => {
      const at = e.text.toLowerCase().indexOf(words[0]);
      const from = Math.max(0, at - 40);
      const snippet = at < 0 ? e.text.slice(0, 100) : (from ? "…" : "") + e.text.slice(from, at + 70) + "…";
      const href = e.page + ".html" + (e.id ? "#" + e.id : "");
      return '<li><a href="' + href + '"><b>' + esc(e.title) + "</b><span>" + esc(e.section) + "</span><small>" + esc(snippet) + "</small></a></li>";
    }).join("") || '<li class="none">No matches</li>';
  });
})();
`;

function guidePage(page, pages, html) {
  const i = pages.indexOf(page);
  const toc = pages
    .map((p) => {
      const current = p === page ? ' class="current" aria-current="page"' : "";
      const subs =
        p === page && p.headings.length
          ? `<ul>${p.headings.map((h) => `<li><a href="#${h.id}">${inline(h.text)}</a></li>`).join("")}</ul>`
          : "";
      return `<li><a href="${p.slug}.html"${current}>${inline(p.title)}</a>${subs}</li>`;
    })
    .join("\n");
  const prev = pages[i - 1];
  const next = pages[i + 1];
  const pager = `<nav class="pager">${
    prev ? `<a class="prev" href="${prev.slug}.html"><small>&larr; Previous</small>${inline(prev.title)}</a>` : ""
  }${next ? `<a class="next" href="${next.slug}.html"><small>Next &rarr;</small>${inline(next.title)}</a>` : ""}</nav>`;
  const title = page.slug === "index" ? GUIDE_TITLE : `${escapeHtml(plainTitle(page.title))} — ${GUIDE_TITLE}`;
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${title}</title>
<style>${guideCss}</style>
</head>
<body>
<header class="topbar"><button id="menu" type="button" aria-controls="side">&#9776; Menu</button><a href="index.html">User Guide</a></header>
<div class="layout">
<nav class="side" id="side" aria-label="Guide">
<a class="brand" href="index.html">${GUIDE_TITLE}</a>
<div class="search"><input id="q" type="search" placeholder="Search the guide…" aria-label="Search the guide" autocomplete="off"><ol id="results"></ol></div>
<ol class="toc">
${toc}
</ol>
<p class="more"><a href="../commands.html">Command reference</a><br><a href="/">&larr; Back to the dashboard</a></p>
</nav>
<main>
${html}
${pager}
</main>
</div>
<script>${guideScript}</script>
</body>
</html>
`;
}

// ---- the user guide ----
mkdirSync(outImgDir, { recursive: true });
const rawGuide = readFileSync(join(repoRoot, "docs", "user-guide.md"), "utf8");
// Which page each heading id lands on, from the headings alone (doc links don't affect them).
const pageOf = (() => {
  const toks = marked.lexer(rawGuide);
  const ps = splitPages(toks, headingIds(toks));
  return new Map(ps.flatMap((p) => p.ids.map((id) => [id, p.slug])));
})();
const guideUrl = (prefix) => (anchor) =>
  anchor && pageOf.has(anchor) ? `${prefix}${pageOf.get(anchor)}.html#${anchor}` : `${prefix}index.html`;

// Links and images inside guide pages are relative to /guide/.
const fromGuide = (target, anchor) =>
  target === "commands.md" ? `../commands.html${anchor ? "#" + anchor : ""}` : target === "user-guide.md" ? guideUrl("")(anchor) : null;
const guideTokens = marked.lexer(rewriteLinks(rawGuide, fromGuide).replaceAll("img/userguide/", "img/"));
const guideIds = headingIds(guideTokens);
const pages = splitPages(guideTokens, guideIds);

idOf = guideIds;
for (const page of pages) {
  let html = rewriteAnchors(marked.parser(page.tokens), page.slug, pageOf);
  if (page.slug === "index") {
    html += `<h2 id="contents">Contents</h2>\n<ol class="contents">${pages
      .slice(1)
      .map((p) => `<li><a href="${p.slug}.html">${inline(p.title.replace(/^\d+\.\s*/, ""))}</a></li>`)
      .join("")}</ol>\n`;
  }
  writeFileSync(join(guideDir, `${page.slug}.html`), guidePage(page, pages, html));
}
writeFileSync(
  join(guideDir, "search-index.json"),
  JSON.stringify(searchEntries(pages).map((e) => ({ ...e, title: plainTitle(e.title), section: plainTitle(e.section) }))),
);

// /guide.html: old links (and bookmarks) forward to the page holding their #anchor.
writeFileSync(
  join(outDir, "guide.html"),
  `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>${GUIDE_TITLE}</title>
<script>
(() => {
  const pageOf = ${JSON.stringify(Object.fromEntries(pageOf))};
  const id = decodeURIComponent(location.hash.slice(1));
  location.replace("guide/" + (pageOf[id] ? pageOf[id] + ".html#" + encodeURIComponent(id) : "index.html"));
})();
</script>
<noscript><meta http-equiv="refresh" content="0; url=guide/index.html"></noscript>
</head>
<body><p>The guide has moved: <a href="guide/index.html">open the user guide</a>.</p></body>
</html>
`,
);

// ---- the command reference (one page) ----
const commandsMd = rewriteLinks(readFileSync(join(repoRoot, "docs", "commands.md"), "utf8"), (target, anchor) =>
  target === "user-guide.md" ? guideUrl("guide/")(anchor) : target === "commands.md" ? `commands.html${anchor ? "#" + anchor : ""}` : null,
).replaceAll("img/userguide/", "guide/img/");
const commandTokens = marked.lexer(commandsMd);
idOf = headingIds(commandTokens);
writeFileSync(join(outDir, "commands.html"), singlePage("Puzzbot Command Reference", marked.parser(commandTokens)));

let copied = 0;
for (const f of readdirSync(srcImgDir)) {
  copyFileSync(join(srcImgDir, f), join(outImgDir, f));
  copied += 1;
}
console.log(`[build-guide] wrote public/guide/ (${pages.length} pages + search index), guide.html, commands.html and ${copied} images`);
