# Extension 1.2.2: reuse the open dashboard tab — design (short)

Date: 2026-11-27. Status: design. Branch `ext-tab-reuse` off `staging`.

## Intent

Dashboard links posted in Discord (the "Open in dashboard" link on a mark-up post, the
pinned Images list) open a new puzzbot.com tab every time, from the Discord desktop app
and from Discord in a browser. During a hunt that piles up copies of the board. With the
extension installed, a link to a hunt whose board is already open should land in that tab.

## Goals

- A new tab arriving at a hunt board (`/hunts/<uid>`, with or without `?puzzle=&image=`)
  for a hunt already open in another tab is folded into the existing tab: the link is
  handed over, that tab and its window come to the front, and the new tab closes.
- The existing tab takes the link **without reloading** (keeps scroll, filters, open
  panels) when both the dashboard and the extension support it; otherwise it navigates
  to the link (a reload) — never a lost link.

## Non-goals

- Links to a hunt that is not open: they open in a new tab as today.
- Other dashboard pages (home, settings, team servers).
- Browsers without the extension.
- Moving the board into the new tab's tab group (the sheet reuse does this; the board is
  not a group member in practice).

## Anti-goals

- Killing a board tab the user opened on purpose. A tab opened **from a dashboard tab**
  (middle-click, "open in new tab" on the board) is never folded in, so two side-by-side
  views of one hunt still work (user, 2026-11-27). Detected as: opener is a dashboard tab
  AND the tab opened in the background. Chrome names the active tab as the opener of a link
  opened from another app (Discord desktop), so the opener alone cannot tell them apart; a
  foreground Ctrl+Shift+click from the board is therefore folded (user, 2026-11-27).
- Losing the link (closing the new tab before the existing one has it).
- A window left empty: a new tab that is its window's only tab is left alone (as the
  sheet reuse already does).

## Approaches

1. **Extension spots the duplicate (chosen, user 2026-11-27).** Same shape as the
   existing files-mode sheet reuse (`maybeReuse` in `extension/src/background.js`): the
   tab registry already knows each board tab's hunt (`lookupHunt`), and `tabs.onUpdated`
   sees the new tab's URL as it starts loading. No new permissions — a new permission
   would disable the extension after the update until the user accepts it.
2. **Dashboard alone (rejected):** a page cannot close a tab it did not open, nor focus
   another tab.
3. **Catch it before the tab opens (rejected):** redirect rules cannot target an existing
   tab; anything earlier than `onUpdated` needs a new permission.

Hand-over: **no reload** (user, 2026-11-27), with reload as the fallback.

## Design

**Extension (1.2.2):**
- On a tab's URL becoming a hunt board for hunt `H` (new arrival only, not a reload or a
  change of query on the same tab), look for another tab showing `H`'s board. Skip when
  the new tab opened in the background from a dashboard tab (`openerTabId`), or it is its
  window's only tab.
- Target choice when several boards of `H` are open: one in the new tab's window first,
  else the most recently used (`lastAccessed`). *(Builder's call.)*
- Hand over: `chrome.tabs.sendMessage(target, {type: "openBoardLink", search})` to the
  dashboard content script; it answers whether the page accepted. On `false`, no answer,
  or an error (old dashboard, script not injected) → `chrome.tabs.update(target, {url})`.
- Then focus the target tab and window, and close the new tab — only after the hand-over
  has succeeded or the fallback navigation has been issued. Serialise with the same
  chain as sheet reuse so two links arriving together cannot close each other's target.
- Bump `manifest.json` to 1.2.2.

**Content script (`dashboard.js`):** on `openBoardLink`, if the page has published
support (a `data-puzzbot-links` attribute the SPA sets), `window.postMessage` the search
string to the page and answer `true`; otherwise answer `false`.

**Dashboard SPA (ships via staging, harmless without the extension):** `HuntBoard` sets
the support attribute while mounted, listens for that message (same-origin, from the
content script), and applies it with `setSearchParams(search)`. The existing deep-link
effect then does the rest (opens the drawer/editor, notices for stale links, clears the
URL). A plain board link with no parameters just brings the tab forward.

## Left to the builder

- Message names, the attribute name, and where the shared "is this a board-tab
  duplicate?" decision lives (a pure function next to `reuseTarget`, unit-tested).
- If the mark-up editor is open in the target tab, the new link replaces it; an
  uncommitted text box is committed first if that is cheap, else accepted as lost
  (marks themselves save as drawn).

## Testing

- Extension (node tests, like the sheet reuse): the duplicate decision — same hunt
  folded; different hunt, opened-from-dashboard, only-tab-in-window, reload of the same
  tab all left alone; target prefers same window. Hand-over falls back to `tabs.update`
  on `false` and on a thrown `sendMessage`; the new tab is closed only after.
- Dashboard (vitest): the message handler accepts only same-origin messages of the right
  shape and passes the search string on.
- Manual (user, stetson): click a Discord link with the board open — same tab, no reload,
  image opens; with the board closed — new tab as today; middle-click a puzzle from the
  board — second tab stays; extension 1.2.2 against the live (older) dashboard — falls
  back to a reload in the same tab.
