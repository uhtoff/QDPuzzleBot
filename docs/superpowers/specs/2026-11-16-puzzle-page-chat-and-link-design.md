# Chat on the puzzle page, and the dashboard's puzzle-page link through the extension — design

Date: 2026-11-16. Status: approved by user in conversation. Ships in extension 1.1. Builds on
`2026-11-16-close-solved-puzzle-pages-design.md` (the `pageTabs` record of hunt pages the
extension opened).

## Intent

1. **Chat on the puzzle page.** On a hunt puzzle page the panel says "not a puzzle". For pages
   the extension opened, it knows the puzzle, so show that puzzle's chat there too.
2. **The ↗ "Puzzle page" link** on the dashboard is a plain link the extension never sees.
   When the extension can handle it, route the click through the extension so the page is
   remembered: Close solved and the chat then cover it too. The user: it "enhances the
   dashboard".

## Non-goals / anti-goals

- Pages opened from Discord, the sheet, or a modified click (ctrl/cmd/shift/middle/right
  "open in new tab"). The user expects to add per-hunt host permission later (`docs/PLANS.md`,
  "Close solved closes hand-opened puzzle pages"), which supersedes this record-keeping.
- Anti-goal: a page tab ever being mistaken for its sheet — tab reuse ("switch to the
  already-open sheet and close the duplicate"), Work on, and Open puzzle/round must never
  pick or close a page tab as the sheet.
- No new permissions.

## Approaches considered

- **Page tabs as flagged registry entries** — *chosen.* The registry already drives the panel
  snapshot, live-message routing (`tabsForChannel`), unread and notifications, so a page tab
  gets all of it. The risk is the sheet-only uses (`findFilesTab`, `reuseTarget`, Close
  solved's sheet path); those exclude `page` entries explicitly, pinned by registry tests.
- A separate page→puzzle map consulted by the panel only — rejected: unread, notifications
  and live routing all key on the registry and would each need a second path.

## Design (decided)

- **Panel on a page tab:** in `refreshTab`, a tab whose URL is **unreadable** (`!tab.url`)
  and that `pageTabs` remembers is looked up by its remembered sheet and registered with
  `page: true`. A readable non-sheet URL (dashboard, etc.) is "not a puzzle" as today; a
  sheet URL is an ordinary sheet tab (the entry is replaced, flag gone).
- **Registry guards:** `findFilesTab` and `reuseTarget` ignore `page` entries; Close solved's
  sheet pass skips `page` entries (pages close through the page pass).
- **New dashboard action `openPage`** `{sheetUrl, puzzleUrl}`: validated like `openPuzzle`
  minus the name. The extension opens `puzzleUrl` as an **active** tab next to the dashboard
  tab (`openerTabId`, sender's window) — what a `target=_blank` click does — and remembers it
  in `pageTabs`. Added to the content script's `ACTIONS`, so the dashboard sees it published.
- **Dashboard:** `PuzzleLinks`' ↗ link keeps its `href`. On a **plain left click** (button 0,
  no ctrl/meta/shift/alt) with `extensionSupports("openPage")` and a `sheet_url`, it prevents
  the default and sends `openPage`. Every other click is a normal link. 1.0.0 copies publish
  no list, so they keep the plain link.
- **Docs:** README "Using it" and the user guide get a line each.

## Plan

1. Registry: `page` guards in `findFilesTab` / `reuseTarget` (tests first, `test/registry.test.js`).
2. Background: page-tab lookup in `refreshTab`; Close solved sheet pass skips `page`;
   `openPage` handler. Validation in `lib/actions.js` + `ACTIONS` in `dashboard.js` (tests
   first: `test/actions.test.js`, `test/dashboard.test.js`).
3. Frontend: `openPage()` and `isPlainClick(event)` in `frontend/src/lib/extension.ts` (tests
   first, `extension.test.ts`); wire into `PuzzleLinks.tsx`. Docs.
4. Verify: extension `npx vitest run`; frontend `npm test`, `npm run build`, and lint/type
   checks CI runs. Manual smoke on test (puzzbot.com).
