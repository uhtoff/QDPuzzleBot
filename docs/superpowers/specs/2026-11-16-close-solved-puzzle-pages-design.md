# Close solved also closes puzzle pages — design

Date: 2026-11-16. Status: approved by user. Ships in extension 1.1 (not yet uploaded).

## Intent

**Close solved tabs** closes solved puzzles' sheet tabs. The user wants it to also close each
solved puzzle's **hunt puzzle page**, so a finished puzzle leaves nothing behind, whether the
page still sits in its tab group or has been dragged out of it (or to another window).

## Goals / non-goals / anti-goals

- **Goal:** close the hunt page tabs that the extension itself opened with **Open puzzle as a
  tab group**, once their puzzle is solved.
- **Non-goal:** pages the user opened any other way (Discord, sheet, dashboard link). Those
  need option 2 below; left for later (`docs/PLANS.md`).
- **Anti-goal:** closing a tab the extension cannot account for. Never match tabs by URL: with
  no host permission, `chrome.tabs.query({url})` *ignores* the filter and returns every tab.

## Constraints

- No new permissions (no `tabs`, no hunt-site host access): no Store permission warning, no
  re-approval for existing users.
- The extension cannot read a hunt-page tab's URL, only its id, window, group and load status.
- Dashboard and server unchanged; no new dashboard action, so no version gating needed.

## Approaches considered

1. **Remember the tabs it opened** — *chosen.* No permissions; misses hand-opened pages.
2. **Optional host access per hunt site** — covers every page, but one grant per user per
   hunt, an alarming Chrome prompt, fiddly URL matching, and a new gated dashboard message.
   Can be layered on top of 1 later.
3. **Restore the `tabs` permission** — covers everything, but brings back the "read your
   browsing history" warning, forces every user to re-approve, and needs a fresh review. Rejected.

## Design (decided)

- **Remember:** when `openPuzzle` *creates* the hunt-page tab, record `tabId →
  {spreadsheetId, gid}` (from the request's `sheetUrl`). Persist in `chrome.storage.session`
  alongside the registry (survives service-worker restarts; cleared on browser restart, when
  tab ids change anyway). Drop the record on `tabs.onRemoved` (and `onReplaced` → move to the
  new id).
- **Close:** `closeSolved` also looks up every remembered page's sheet via the existing
  by-sheet route — **even if that sheet's tab is no longer open** — and closes the page tabs
  whose puzzle is solved, alongside the sheet tabs. The existing "never leave a window empty"
  planning (`planWindowClose`) covers page tabs too.
- **Navigated pages (user decision):** a remembered tab is closed **wherever it has navigated
  since** — the extension cannot tell an answer-submit reload from reuse for something else,
  and Close solved is an explicit click. Only skip a tab that is mid-load at the moment of
  closing (same caution as sheet tabs).
- **Docs:** one line each in `docs/user-guide.md` and `extension/README.md`.

## Left to the builder

- Where the page-tab map lives (a small pure module under `extension/src/lib/`, unit-tested
  with the existing node test runner, is the expected shape).
- Exact closing order within `closeSolved`, as long as the per-window empty-window rule holds.
