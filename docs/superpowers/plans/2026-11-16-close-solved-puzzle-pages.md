# Close solved closes button-opened puzzle pages — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close solved tabs also closes hunt puzzle pages that the extension opened via Open puzzle as a tab group, once their puzzle is solved.

**Architecture:** A small pure module remembers `pageTabId → {spreadsheetId, gid}` for page tabs `openPuzzle` creates; it is persisted in `chrome.storage.session` with the registry. `closeSolved` looks up each remembered sheet (whether or not its sheet tab is open) and closes solved page tabs through the same per-window planning as sheet tabs.

**Tech Stack:** Chrome MV3 extension, plain ES modules, vitest (`extension/`).

**Spec:** `docs/superpowers/specs/2026-11-16-close-solved-puzzle-pages-design.md`

## Global Constraints

- No manifest permission changes; never use `chrome.tabs.query({url})` for hunt pages (it ignores the filter without host access and returns every tab).
- No dashboard, server or `validateAction` changes.
- A remembered page tab is closed wherever it has navigated; skip it only while `status === "loading"`.
- A window is never left empty (existing `planWindowClose` + blank tab).
- Run tests from `extension/`: `timeout 120 npx vitest run <file> > /tmp/ext-test.log 2>&1`.

## Review Focus

1. Sheet tab closed by hand, page still open, puzzle solved → page still closes (lookup must not depend on the registry). Covered by Task 1 `remembered sheets` test + Task 2 smoke step.
2. A page and its sheet are the only tabs in a window → one blank tab is created, not two, and both close. Task 1 `canClose`/`planWindowClose` test with mixed kinds.
3. Page tab closed by the user before Close solved runs → no error, forgotten (`remove`), not "closed" again. Task 1 `remove` test.
4. Two remembered pages for the same puzzle (button clicked after the group was dissolved) → both close; one lookup per sheet. Task 1 `sheets()` dedupe test.
5. Service worker restarts between Open puzzle and Close solved → map restored from session storage. Task 1 round-trip test (`entries()` → `createPageTabs(entries)`); wiring inspection + smoke.

---

### Task 1: `pageTabs` module and a kind-aware close check

**Files:**
- Create: `extension/src/lib/pageTabs.js`
- Modify: `extension/src/lib/closeSolved.js` (add `canClose`)
- Test: `extension/test/pageTabs.test.js`, `extension/test/closeSolved.test.js`

**Interfaces:**
- Produces (`pageTabs.js`):
  - `createPageTabs(initial = [])` → `{ add(tabId, {spreadsheetId, gid}), remove(tabId) → boolean, replace(oldId, newId) → boolean, entries() → Array<[tabId, {spreadsheetId, gid}]>, sheets() → Array<{spreadsheetId, gid}> }`. `sheets()` is deduped by `spreadsheetId` + `gid` (gid `null` allowed). `initial` is an `entries()` result.
  - `pageTabsFor(entries, spreadsheetId, gid)` → `number[]` tab ids remembered for that sheet.
- Produces (`closeSolved.js`): `canClose(item, tab)` where `item` is `{kind: "sheet" | "page", tabId, spreadsheetId}`; `"sheet"` → existing `shouldCloseTab(tab, item.spreadsheetId)`; `"page"` → `!!tab && tab.status !== "loading"`.

- [ ] **Step 1: Write the failing tests** in `test/pageTabs.test.js`:
  - `add then entries round-trips through createPageTabs(entries)`: add `(7, {spreadsheetId:"A", gid:3})`, rebuild from `entries()`, `pageTabsFor(rebuilt.entries(), "A", 3)` equals `[7]`.
  - `remove forgets the tab`: after `remove(7)` → `true`, `pageTabsFor(..., "A", 3)` equals `[]`; second `remove(7)` → `false`.
  - `replace moves the record`: `replace(7, 9)` → `true`; `pageTabsFor` equals `[9]`.
  - `sheets dedupes and keeps gid apart`: add `(1,{A,3})`, `(2,{A,3})`, `(3,{A,4})`, `(4,{B,null})` → `sheets()` has length 3; `pageTabsFor(entries,"A",3)` equals `[1,2]`.
  And in `test/closeSolved.test.js`, `describe("canClose")`:
  - page item: `{id:5, status:"complete", url: undefined}` → `true`; `status:"loading"` → `false`; `null` → `false`.
  - sheet item delegates: sheet tab on `url("A")` with spreadsheetId `"A"` → `true`; on `url("B")` → `false`.
  - mixed window: `planWindowClose([{id:1,windowId:1},{id:2,windowId:1}], [{id:1,windowId:1},{id:2,windowId:1}], 1)` → `{closing:[1,2], needBlank:true}` (sheet + page as the window's only tabs → exactly one blank).
- [ ] **Step 2: Run** `timeout 120 npx vitest run test/pageTabs.test.js test/closeSolved.test.js > /tmp/ext-test.log 2>&1` — expect FAIL (module/export missing).
- [ ] **Step 3: Implement** `pageTabs.js` (a `Map` keyed by tab id) and `canClose` in `closeSolved.js`.
- [ ] **Step 4: Run** the same command — expect PASS. Mutation check: make `canClose` page branch return `true` unconditionally → the `loading` assertion goes red; restore.
- [ ] **Step 5: Commit** `feat(extension): remember button-opened puzzle pages; kind-aware close check`.

### Task 2: Wire into background, docs

**Files:**
- Modify: `extension/src/background.js` (`ready`, `persist`, `tabs.onRemoved`, new `tabs.onReplaced`, `openTabs`, `openPuzzle`, `closeSolved`)
- Modify: `extension/README.md` (Using it → Close solved line), `docs/user-guide.md` (Close solved tabs line)

**Interfaces:**
- Consumes: `createPageTabs`, `pageTabsFor` (`lib/pageTabs.js`); `canClose`, `planWindowClose` (`lib/closeSolved.js`); existing `parseSheetUrl`, `apiGet`, `bySheetPath`.
- Storage key: `pageTabEntries` in `chrome.storage.session`, written by `persist()` and read in `ready` beside `registryEntries`.

- [ ] **Step 1: State.** Module-level `let pageTabs = createPageTabs();` restored in `ready` from `pageTabEntries`; `persist()` also writes `pageTabEntries: pageTabs.entries()`. `onRemoved` calls `pageTabs.remove(tabId)`. Add `chrome.tabs.onReplaced.addListener((added, removed) => …)` → `await ready; if (pageTabs.replace(removed, added)) persist();`.
- [ ] **Step 2: Record.** `openTabs(urls, windowId, onPageTab)` — call `onPageTab?.(created.id)` where the non-sheet tab is created. `openPuzzle` passes `(id) => { pageTabs.add(id, parseSheetUrl(urls[0])); persist(); }` (`urls[0]` is the validated sheet URL). `openRound` unchanged.
- [ ] **Step 3: Close.** In `closeSolved`, build `solved` as `{kind, tabId, spreadsheetId}` items: existing sheet results become `kind: "sheet"`; then for each `pageTabs.sheets()` call `apiGet(bySheetPath({spreadsheetId, gid}))` (in the same `Promise.allSettled`), and for a solved puzzle push `{kind:"page", tabId, spreadsheetId}` for each id in `pageTabsFor(pageTabs.entries(), spreadsheetId, gid)`. Page lookups do **not** touch the registry. Replace every `shouldCloseTab(tab, spreadsheetId)` re-check in the window loop with `canClose(item, tab)` (look the item up by tab id). Window planning is otherwise unchanged.
- [ ] **Step 4: Docs.** README "Using it" Close solved bullet and user guide: "also closes puzzle pages opened with Open puzzle as a tab group (pages opened any other way are left open)".
- [ ] **Step 5: Verify.** `timeout 180 npx vitest run > /tmp/ext-test.log 2>&1` — all pass. `node --check src/background.js`. Background wiring has no unit tests: it is inspection-only plus this manual smoke (build with `bash package.sh`, unzip over `~\puzzbot-ext`, reload) on test (puzzbot.com), files-mode hunt:
  1. Open puzzle (button) on an unsolved puzzle → solve it → Close solved: sheet **and** page close, group gone.
  2. Open puzzle → drag the page out of the group (and to another window) → solve → Close solved: page closes.
  3. Open puzzle → close the sheet tab by hand → solve → Close solved: page closes.
  4. Open puzzle → navigate the page elsewhere → solve → Close solved: page closes.
  5. Puzzle page opened from a dashboard link (not the button) → solve → Close solved: page stays.
  6. Unsolved puzzle's button-opened page → Close solved: stays.
- [ ] **Step 6: Commit** `feat(extension): Close solved also closes button-opened puzzle pages`.
