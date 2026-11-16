# User guide as a small docs site — design

Date: 2026-11-16. Status: approved by user in conversation ("That looks much better").

## Intent

`docs/user-guide.md` (620 lines) renders to one long `/guide.html`. The user finds it too
long for a single document and asked for "tabs … or however the kids are arranging these
things nowadays", handing over the how. Goal: easy to find your way around, mid-hunt on a
phone as well as reading through on a laptop.

## Decided

- **One page per `##` section**, split at build time from the one markdown file (still the
  only source; editing is unchanged). Pages: `guide/index.html` (the intro before the first
  `##`) plus `guide/<slug>.html`, slug = the heading minus its `N. ` number and anything after
  ` — `, e.g. `the-basics`, `the-web-dashboard`.
- **Sticky left sidebar** listing all sections, the current one expanded to its `###`
  headings; plus links to the command reference and back to the dashboard. On narrow screens
  it collapses behind a **Menu** button.
- **Previous / Next** links at the foot of each page.
- **Search** across the whole guide: a JSON index (one entry per `###`, or per `##` with
  none) built alongside the pages, matched in the browser. No external service.
- **Links keep working.** Heading ids are computed over the whole document exactly as today
  (GitHub slugs, `-1` for repeats), so ids do not change. In-guide `#anchor` links are
  rewritten to `page.html#anchor` when the target is on another page. `/guide.html` stays as
  a stub that forwards `#anchor` to the right page (map built at build time), else to the
  overview. The dashboard's Guide link points at `/guide/index.html`.
- Same look as today (fonts, colours). `commands.html` is untouched apart from links.

## Not doing

Rewriting or reordering guide content; dark mode; splitting `commands.md`.

## Approaches considered

- **Docs-site layout (chosen)** — what current docs tools do; scales with the dashboard
  section being half the guide.
- Client-side tabs on one page — cramped labels on a phone; the dashboard tab would still be
  half the guide; Ctrl-F only sees one tab.
- Pinned contents bar on the single page — keeps Ctrl-F, but the page stays 620 lines.

## Plan

1. Pure helpers in `frontend/guide-lib.mjs` (section slug, split tokens into pages, heading
   ids for the whole document, anchor rewriting, search entries), tested in
   `frontend/guide-lib.test.mjs` (vitest).
2. `frontend/build-guide.mjs` renders the pages, sidebar, prev/next, search index and the
   `guide.html` stub with them; images move to `img/` relative to `guide/` pages.
3. Dashboard Guide link → `/guide/index.html`. Build, check pages and links, screenshot the
   overview and dashboard page (desktop + phone) for the user.
