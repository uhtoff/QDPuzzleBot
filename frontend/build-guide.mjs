// Generate the static docs served by the dashboard at /guide.html and
// /commands.html.
//
// Runs as npm's prebuild hook, so every `npm run build` (including the one in
// start.sh on deploy) re-renders the markdown into frontend/public/, which Vite
// copies verbatim into dist. Output is gitignored — the markdown is the source
// of truth.
import { copyFileSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { marked } from "marked";

const frontendDir = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(frontendDir, "..");
const srcImgDir = join(repoRoot, "docs", "img", "userguide");
const outDir = join(frontendDir, "public");
const outImgDir = join(outDir, "guide", "img");

// The docs we publish. Anything not listed here is repo-only (AGENTS.md,
// dashboard.md, …) — see rewriteLinks.
const PAGES = [
  { md: "user-guide.md", html: "guide.html", title: "PuzzleHuntBot User Guide" },
  { md: "commands.md", html: "commands.html", title: "PuzzleHuntBot Command Reference" },
];
const published = new Map(PAGES.map((p) => [p.md, p.html]));

// Relative *.md links work in the repo but 404 on the dashboard, where only the
// PAGES above exist. Point links at a published page to its .html sibling, and
// unwrap the rest to plain text so no dead link ships. Absolute, external and
// anchor-only links are left alone.
const MD_LINK = /\[([^\]]+)\]\((?!https?:|\/|#)([^)\s]+?\.md)(#[^)\s]*)?\)/g;
const rewriteLinks = (md) =>
  md.replace(MD_LINK, (whole, text, target, anchor = "") => {
    const html = published.get(target.replace(/^(\.\/|\.\.\/)+/, ""));
    return html ? `[${text}](${html}${anchor})` : text;
  });

// marked dropped automatic heading ids in v13, which left every in-page #anchor
// in the markdown pointing at nothing. Re-add them with GitHub's slug rules,
// which is what those anchors were written against.
const slug = (text) =>
  text
    .toLowerCase()
    .replace(/[`*_~]/g, "")
    .replace(/[^\w\- ]+/g, "")
    .trim()
    .replace(/\s+/g, "-");

// Duplicate headings get -1, -2, … appended, as GitHub does. Reset per page.
const seen = new Map();
marked.use({
  renderer: {
    heading({ tokens, depth, text }) {
      const base = slug(text);
      const n = seen.get(base) ?? 0;
      seen.set(base, n + 1);
      const id = n === 0 ? base : `${base}-${n}`;
      return `<h${depth} id="${id}">${this.parser.parseInline(tokens)}</h${depth}>\n`;
    },
  },
});

const css = `
  body { font-family: system-ui, -apple-system, 'Segoe UI', Arial, sans-serif;
         font-size: 16px; line-height: 1.55; color: #1a1a2e;
         max-width: 880px; margin: 0 auto; padding: 24px 20px 80px; }
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

const page = (title, body) => `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${title}</title>
<style>${css}</style>
</head>
<body>
<p class="back"><a href="/">&larr; Back to the dashboard</a></p>
${body}
</body>
</html>
`;

mkdirSync(outImgDir, { recursive: true });
for (const { md, html, title } of PAGES) {
  // The markdown references images as img/userguide/…; serve them at guide/img/…
  // (relative to the page, so it works behind any proxy prefix).
  const src = readFileSync(join(repoRoot, "docs", md), "utf8").replaceAll(
    "img/userguide/",
    "guide/img/",
  );
  seen.clear();
  writeFileSync(join(outDir, html), page(title, marked.parse(rewriteLinks(src))));
}
let copied = 0;
for (const f of readdirSync(srcImgDir)) {
  copyFileSync(join(srcImgDir, f), join(outImgDir, f));
  copied += 1;
}
console.log(
  `[build-guide] wrote ${PAGES.map((p) => `public/${p.html}`).join(", ")} and ${copied} images`,
);
