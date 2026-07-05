// Generate the static user guide served by the dashboard at /guide.html.
//
// Runs as npm's prebuild hook, so every `npm run build` (including the one in
// start.sh on deploy) re-renders docs/user-guide.md into frontend/public/,
// which Vite copies verbatim into dist. Output is gitignored — the markdown
// is the source of truth.
import { copyFileSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { marked } from "marked";

const frontendDir = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(frontendDir, "..");
const srcMd = join(repoRoot, "docs", "user-guide.md");
const srcImgDir = join(repoRoot, "docs", "img", "userguide");
const outDir = join(frontendDir, "public");
const outImgDir = join(outDir, "guide", "img");

// The markdown references images as img/userguide/…; serve them at guide/img/…
// (relative to /guide.html, so it works behind any proxy prefix).
const md = readFileSync(srcMd, "utf8").replaceAll("img/userguide/", "guide/img/");
const body = marked.parse(md);

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

const html = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PuzzleHuntBot User Guide</title>
<style>${css}</style>
</head>
<body>
<p class="back"><a href="/">&larr; Back to the dashboard</a></p>
${body}
</body>
</html>
`;

mkdirSync(outImgDir, { recursive: true });
writeFileSync(join(outDir, "guide.html"), html);
let copied = 0;
for (const f of readdirSync(srcImgDir)) {
  copyFileSync(join(srcImgDir, f), join(outImgDir, f));
  copied += 1;
}
console.log(`[build-guide] wrote public/guide.html and ${copied} images`);
