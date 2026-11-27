# Extension 1.2.2: reuse the open dashboard tab — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A dashboard board link opened in a new tab, for a hunt whose board is already open, is handed to the existing tab (no reload when supported), which comes to the front, and the new tab closes.

**Architecture:** Two pure extension modules (the "is this a duplicate, and of which tab" decision, and the hand-over sequence with its reload fallback) wired into `background.js`'s existing `tabs.onUpdated` listener; the dashboard content script relays the hand-over to the page; the SPA's `HuntBoard` publishes support and applies the link through its existing deep-link effect.

**Tech Stack:** Chrome/Firefox MV3 extension (plain ES modules, vitest in `extension/`), React + react-router SPA (vitest in `frontend/`).

**Spec:** `docs/superpowers/specs/2026-11-27-extension-board-tab-reuse-design.md`

## Global Constraints

- No new entries in `manifest.json` `permissions` / `host_permissions` / `optional_*` (a new permission disables the extension after update until accepted).
- `extension/manifest.json` `version` → `1.2.2`. Do **not** touch `frontend/public/extension/versions.json` (`latest` moves only at store release; `versionsFile.test.js` enforces it).
- Never close the new tab before the hand-over succeeded or the fallback `tabs.update` was issued.
- Never fold a tab whose opener is a tab on the dashboard origin, nor a tab that is its window's only tab.
- Message names (fixed here, used across tasks): background → content script `{ type: "openBoardLink", search }`; content script → page `window.postMessage({ source: "puzzbot-extension", action: "openBoardLink", search }, location.origin)`; page support flag `document.documentElement.dataset.puzzbotLinks === "1"`.
- Tests: `cd extension && timeout 120 npx vitest run <file> > /tmp/ext.log 2>&1`; `cd frontend && timeout 180 npx vitest run <file> > /tmp/fe.log 2>&1`. `node_modules` in both are symlinks to the main checkout (already in place).

## Review Focus

1. **The same link clicked twice** (editor closed in between) opens the image again — `HuntBoard` clears `searchParams` and `deepMediaId` after use, so a repeat is a fresh change. Task 4 test pins the parser; manual check in Task 5.
2. **Two links arriving together** must not close each other's target — hand-overs are serialised on the existing `reuseChain` (Task 3).
3. **A board reloaded, or its query changed by the SPA itself** (`setSearchParams({})` after a deep link) is never treated as a new arrival — Task 1 test `ignores the same hunt as before`.
4. **Target tab discarded / asleep or opened before the update** (no content script): `sendMessage` rejects → reload fallback — Task 2 test `falls back when sendMessage throws`.
5. **A link for another dashboard origin** (live vs test) is never folded into the configured origin's board — Task 1 test `ignores other origins`.

---

### Task 1: The duplicate decision

**Files:**
- Create: `extension/src/lib/boardReuse.js`
- Test: `extension/test/boardReuse.test.js`

**Interfaces:**
- Consumes: `parseHuntBoardUrl(url, origin) -> string | null` from `extension/src/lib/origin.js`.
- Produces: `boardReuseTarget({ newTab, beforeUid, tabs, origin }) -> number | null`
  - `newTab: { id: number, url: string, windowId: number, openerTabId?: number }`
  - `beforeUid: string | undefined` — the hunt uid the registry held for this tab before the update (board entries only).
  - `tabs: Array<{ id, url, windowId, lastAccessed? }>` — every open tab (`chrome.tabs.query({})`).
  - returns the target tab id, or `null` to leave the new tab alone.

- [ ] **Step 1: Write the failing tests** in `extension/test/boardReuse.test.js`. Origin `"https://dash.test"`; board URL helper `b = (uid, q = "") => \`https://dash.test/hunts/${uid}${q}\``. Cases (each its own `it`):
  - `folds a new board tab into an open board of the same hunt`: tabs `[{id:1,url:b("h1"),windowId:9,lastAccessed:5}, {id:2,url:b("h1","?image=50"),windowId:9}]`, newTab id 2, beforeUid undefined → `1`.
  - `ignores a different hunt`: existing board `h2`, new `h1` → `null`.
  - `ignores the same hunt as before` (reload / SPA query change): beforeUid `"h1"` → `null`.
  - `ignores a tab opened from a dashboard tab`: newTab `openerTabId: 1` where tab 1 is any `https://dash.test/...` URL → `null`; with opener a `https://discord.com/channels/...` tab → `1`.
  - `ignores the only tab in its window`: new tab alone in window 8, existing board in window 9 → `null`.
  - `ignores non-board and other-origin URLs`: new tab `https://other.test/hunts/h1` → `null`; new tab `https://dash.test/hunts/h1/settings` → `null`.
  - `prefers the new tab's window, then the most recently used`: boards of h1 in window 7 (`lastAccessed: 100`) and window 9 (`lastAccessed: 1`), new tab in window 9 → the window-9 tab; same setup with the new tab in window 5 (plus another tab there) → the `lastAccessed: 100` tab. Use two candidates in each so a wrong ordering disagrees.

- [ ] **Step 2: Run to verify they fail**
  Run: `cd extension && timeout 120 npx vitest run test/boardReuse.test.js > /tmp/ext.log 2>&1` — Expected: FAIL (module not found).

- [ ] **Step 3: Implement `boardReuseTarget`** in `extension/src/lib/boardReuse.js`. Order of checks: parse uid (null → null); `beforeUid === uid` → null; opener tab found in `tabs` whose `new URL(url).origin === origin` → null (catch bad URLs as "not dashboard"); window tab count ≤ 1 → null; candidates = other tabs parsing to the same uid; sort same-window first, then `lastAccessed` descending (missing = 0).

- [ ] **Step 4: Run to verify they pass** — same command, Expected: all pass.

- [ ] **Step 5: Commit** `feat(extension): decide when a new board tab duplicates an open one`

### Task 2: The hand-over sequence

**Files:**
- Modify: `extension/src/lib/boardReuse.js`
- Test: `extension/test/boardReuse.test.js`

**Interfaces:**
- Produces: `handOverBoardLink({ targetId, newTabId, url, api, timeoutMs = 1500 }) -> Promise<"handed" | "reloaded">`
  - `api: { sendMessage(tabId, msg) -> Promise<unknown>, update(tabId, props) -> Promise, focus(tabId) -> Promise, remove(tabId) -> Promise }`
  - Sends `{ type: "openBoardLink", search: new URL(url).search }`. Response `=== true` → `"handed"`. Anything else, a rejection, or no answer within `timeoutMs` → `await api.update(targetId, { url })` → `"reloaded"`. Then `await api.focus(targetId)`, then `api.remove(newTabId)`. Errors from `focus`/`remove` are swallowed.

- [ ] **Step 1: Write the failing tests** (recording fakes: each api fn pushes `[name, ...args]` to a shared `calls` array):
  - `hands the search over and closes the new tab after`: sendMessage resolves `true` → returns `"handed"`; `calls` equal `[["sendMessage", 1, {type:"openBoardLink", search:"?puzzle=3&image=50"}], ["focus", 1], ["remove", 2]]` (url `https://dash.test/hunts/h1?puzzle=3&image=50`, target 1, new 2). No `update`.
  - `falls back to navigating when the page declines`: resolves `false` → `"reloaded"`; `update(1, {url})` appears before `focus` and `remove`.
  - `falls back when sendMessage throws`: rejects `new Error("Receiving end does not exist")` → `"reloaded"`, same order.
  - `falls back when the page never answers`: sendMessage returns a never-settling promise, `timeoutMs: 10` → `"reloaded"`.
  - `a plain board link hands over an empty search`: url `b("h1")` → message `search: ""`.

- [ ] **Step 2: Run to verify they fail** — Expected: FAIL (`handOverBoardLink` not exported).
- [ ] **Step 3: Implement `handOverBoardLink`** (a `Promise.race` against a timer resolving `undefined`; same shape as `withTimeout` in `background.js`).
- [ ] **Step 4: Run to verify they pass.**
- [ ] **Step 5: Commit** `feat(extension): hand a board link to the open tab, reloading as a fallback`

### Task 3: Wire it in (content script, background, manifest)

**Files:**
- Modify: `extension/src/dashboard.js`, `extension/test/dashboard.test.js`
- Modify: `extension/src/background.js` (the `chrome.tabs.onUpdated` listener near line 767; reuse `reuseChain` / `focusTab`)
- Modify: `extension/manifest.json` (`"version": "1.2.2"`)

**Interfaces:**
- Consumes: `boardReuseTarget`, `handOverBoardLink` (Tasks 1–2); `parseHuntBoardUrl`, `getOrigin()`, `registry`, `focusTab(tab)` in `background.js`.

- [ ] **Step 1: Write the failing content-script tests** in `dashboard.test.js`. Extend the stub: `chrome.runtime.onMessage = { addListener: (fn) => (onRuntime = fn) }`; `win.postMessage = vi.fn()`. Tests:
  - `relays a board link when the page supports it`: `dataset.puzzbotLinks = "1"`; `onRuntime({type:"openBoardLink", search:"?image=5"}, {}, respond)` → `win.postMessage` called with `({source:"puzzbot-extension", action:"openBoardLink", search:"?image=5"}, "https://dash.test")`; `respond` called with `true`.
  - `declines when the page has not published support`: delete `dataset.puzzbotLinks` → no postMessage, `respond(false)`.
  - `ignores other messages and a non-string search`: `{type:"other"}` and `{type:"openBoardLink", search: 5}` → `respond` not called with `true`, no postMessage.
- [ ] **Step 2: Run** `test/dashboard.test.js` — Expected: the new tests FAIL.
- [ ] **Step 3: Implement** the `chrome.runtime.onMessage` listener in `dashboard.js` (synchronous `sendResponse`; return nothing). Do not add `openBoardLink` to `ACTIONS` — that set is the page→extension direction.
- [ ] **Step 4: Run** — Expected: pass, and the existing "lists exactly the actions it relays" test still passes unchanged.
- [ ] **Step 5: Wire `background.js`.** In the `tabs.onUpdated` listener, when `info.url` is set: capture `beforeUid` from `registry.get(tabId)` **before** `refreshTab` (`entry?.hunt_chat ? entry.hunt_uid : undefined` — same test as `huntCacheValid`); then queue on `reuseChain` an async step that: `getOrigin()`; `chrome.tabs.query({})`; `boardReuseTarget(...)`; on a target, `handOverBoardLink` with `api` = `chrome.tabs.sendMessage`, `chrome.tabs.update`, `focus: (id) => chrome.tabs.get(id).then(focusTab)`, `chrome.tabs.remove`. Re-check the target still exists (`chrome.tabs.get`) before handing over. The existing sheet `isNewArrival`/`queueReuse` path is unchanged.
- [ ] **Step 6: Bump** `manifest.json` to `1.2.2`.
- [ ] **Step 7: Run the whole extension suite** `cd extension && timeout 180 npx vitest run > /tmp/ext.log 2>&1` — Expected: all pass, skipped count unchanged from before the branch.
- [ ] **Step 8: Commit** `feat(extension): fold a duplicate board tab into the open one (1.2.2)`

### Task 4: Dashboard takes the link without reloading

**Files:**
- Modify: `frontend/src/lib/extension.ts`, `frontend/src/lib/extension.test.ts`
- Modify: `frontend/src/pages/HuntBoard.tsx`

**Interfaces:**
- Produces: `boardLinkFromMessage(event: { source: unknown; origin: string; data: unknown }, win: unknown, origin: string): string | null` — returns `data.search` when `event.source === win`, `event.origin === origin`, `data.source === "puzzbot-extension"`, `data.action === "openBoardLink"`, `search` is a string that is `""` or starts with `"?"`, length ≤ 2000; else `null`.

- [ ] **Step 1: Write the failing tests** in `extension.test.ts`: accepts `"?puzzle=3&image=50"` and `""`; rejects wrong source window, wrong origin, `source: "puzzbot-dashboard"`, wrong action, numeric search, `"https://evil/x"`, a 2001-char search.
- [ ] **Step 2: Run** `cd frontend && timeout 180 npx vitest run src/lib/extension.test.ts > /tmp/fe.log 2>&1` — Expected: FAIL.
- [ ] **Step 3: Implement** `boardLinkFromMessage`.
- [ ] **Step 4: Run** — Expected: pass.
- [ ] **Step 5: Wire `HuntBoard.tsx`:** one `useEffect` (deps `[setSearchParams]`) that sets `document.documentElement.dataset.puzzbotLinks = "1"`, adds a `message` listener calling `boardLinkFromMessage(e, window, location.origin)` and, on a non-null result, `setSearchParams(new URLSearchParams(search))`; cleanup removes the listener and `delete`s the attribute. The existing deep-link effect does the rest; a plain `""` link changes nothing (the tab is just focused).
- [ ] **Step 6: Check** `cd frontend && timeout 300 npx tsc -b > /tmp/tsc.log 2>&1` (exit 0) and the full `npx vitest run` (all pass).
- [ ] **Step 7: Commit** `feat(dashboard): take a board link from the extension without reloading`

### Task 5: Hand-over to the user

- [ ] Record in `.ai/active/` (session summary + a manual checklist): the spec's manual tests, plus Review Focus 1 (same link twice) and 2 (two links quickly). Test path: merge to staging (push = test-site deploy, user's say-so), load the unpacked 1.2.2 build over the fixed per-machine folder, and check against puzzbot.com; against live (older dashboard) the fallback reload applies.
- [ ] Release steps (versions.json `adds` line + `latest`, store uploads, Firefox xpi) are **not** in this plan — they follow the existing README release flow once the user is happy.
