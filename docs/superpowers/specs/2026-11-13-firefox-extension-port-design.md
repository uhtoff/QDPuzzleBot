# Puzzle Chat extension: Firefox port (design)

Date: 2026-11-13. Branch: `firefox-port` (from `staging` @ 331e117).
Builds on `2026-11-07-puzzle-chat-extension-design.md`.

## Goal

The Puzzle Chat extension runs in desktop Firefox with the same chat panel and dashboard
buttons as in Chrome. One source tree serves both browsers, and the Chrome build and its
Store listing stay unchanged.

## Decisions (agreed with the user, 2026-11-13)

1. **Websocket origin: accept any well-formed `moz-extension://` origin.** Firefox gives each
   install a random UUID origin, so it cannot go in an exact allowlist. Exact entries in
   `web.extension_origins` are unchanged and still needed for Chrome. A web page can never send
   a `moz-extension://` origin, so the check still blocks the cross-site websocket hijacking it
   exists for. The accepted cost is that another installed Firefox add-on could open the socket.
   The ticket path stays in place: a cookieless socket still has to redeem a single-use ticket.
2. **Losing auto-open is accepted.** Firefox only lets `sidebarAction.open()` run inside a
   user-action handler (toolbar click, menu, shortcut). It does not count clicks on web pages or
   notifications. So "Work on this puzzle" and notification clicks switch the tab and fill in
   the sidebar if it is open, but cannot open it. The toolbar button toggles the sidebar.
3. **Feature gaps are hidden where needed.** The minimum Firefox version is **140.0** (ESR).
   That gives `tabs.group` (138), `tabGroups` (139) and `data_collection_permissions` (140), so
   every current dashboard action works and nothing needs hiding now. The `ACTIONS` gate stays
   the mechanism for hiding a button if a later action is Chrome-only.

## Shape

- `extension/manifest.json` stays the Chrome manifest and the single source. The Firefox
  manifest is **derived** from it at package time by a pure, tested transform. Nothing is
  hand-maintained twice.
- The difference between the side panel and the sidebar lives in one small module,
  `src/lib/panelHost.js`, with the browser APIs injected. `background.js` calls it in place of
  `chrome.sidePanel.*`.
- All other `chrome.*` calls are unchanged (Firefox MV3 provides the promise-returning `chrome`
  namespace).

## Firefox manifest (derived)

From the Chrome manifest:
- drop `side_panel`, `minimum_chrome_version`, `options_page`, and `"sidePanel"` from `permissions`
- `background` → `{ "scripts": ["src/background.js"], "type": "module" }`
- add `sidebar_action`: `{ "default_panel": "src/panel.html", "default_title": "Puzzle Chat", "default_icon": <the action's icons>, "open_at_install": false }`
- add `options_ui`: `{ "page": "src/options.html", "open_in_tab": true }`
- add `browser_specific_settings.gecko`: `{ "id": "puzzle-chat@puzzbot.com", "strict_min_version": "140.0", "data_collection_permissions": { "required": ["authenticationInfo", "websiteContent", "personalCommunications"] } }`.
  The categories mirror the Chrome Store disclosure (`store/listing.md`, "Data use disclosure").

## Server

`bot/web/api.py`: one predicate `_is_extension_origin(origin)`. It is true for an exact
`web.extension_origins` entry, or for an origin matching
`^moz-extension://[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`. Both the
origin gate (`_ws_origin_allowed`) and the ticket path in `ws_activity` use it. No config change
is needed for Firefox.

## Google Sheets host permission

Firefox 127+ grants `host_permissions` at install, but the user can revoke them in
`about:addons`. Without access the extension sees no sheet URLs, and every tab reads
"not a puzzle". Two mitigations:
- the options page shows an "Allow access to Google Sheets" button when access is missing
- `runtime.onInstalled` opens the options page when access is missing

In Chrome access is always granted, so neither appears there.

## Distribution

AMO **unlisted**. Mozilla signs it and the team installs from the signed `.xpi` link.
`package.sh firefox` builds `dist/firefox/` (for `about:debugging` → Load Temporary Add-on) and
`dist/puzzle-chat-firefox-<version>.zip` (for upload). AMO upload and signing are manual steps
for the user.

## Out of scope

Firefox for Android (it has no sidebar). Safari. Changes to the Chrome manifest or Chrome Store
listing. A version bump: the shared `background.js` change reaches Chrome at its next Store
upload, as normal.

## Acceptance

- `npm test` (vitest) and the focused pytest files pass. `basedpyright bot/` reports 0 errors.
- `package.sh` with no argument produces the same Chrome zip contents as before. `package.sh firefox`
  produces a manifest that `web-ext lint` (if available) accepts without errors.
- Manual Firefox smoke test (user): toolbar button toggles the sidebar; the sidebar follows the
  active sheet tab; live messages arrive; a reply posts; Work/Open round/Open puzzle/Close
  solved work from the dashboard; the options page origin save prompts for permission.
