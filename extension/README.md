# Puzzle Chat extension

A Chrome (Manifest V3) extension that puts the Discord channel of the puzzle you are
looking at in a side panel next to the Google Sheet, and adds a few dashboard buttons for
opening puzzles. It talks to the puzzbot dashboard using your existing dashboard login; it
holds no Discord credentials of its own. Design: `docs/superpowers/specs/2026-11-07-puzzle-chat-extension-design.md`.
For the user-facing description see the "Chrome extension" section of `docs/user-guide.md`.
It also runs in Firefox; see "Firefox" below.

Requires Chrome 116 or later.

## Install from the Chrome Web Store

Team members install the published build from the Store: <https://chromewebstore.google.com/detail/jadkkcgfeaadaobdmcgcafikblnkiand>. It is unlisted, so it
is reachable only by that link, not by searching the Store.

## Install (unpacked)

1. Open `chrome://extensions`, switch on **Developer mode**.
2. **Load unpacked** and pick this `extension/` folder.
3. Note the extension **ID** shown on its card. For an unpacked extension it depends on the
   folder path (unless a `"key"` is added to the manifest), so it differs between machines.
4. Add that ID to the server (see below), then restart the bot.
5. Open the extension's **Options** and check the dashboard origin.
6. Be logged in to the dashboard in the same Chrome profile (the extension reuses that session).

## Firefox

The same source runs in desktop Firefox 140 or later (not Firefox for Android, which has no
sidebar). `manifest.json` is the Chrome manifest; the Firefox one is generated from it by
`scripts/firefoxManifest.js` at package time, so never edit a copy under `dist/`.

- **Try it locally:** `bash package.sh firefox`, then in Firefox open
  `about:debugging#/runtime/this-firefox`, **Load Temporary Add-on** and pick
  `dist/firefox/manifest.json`. It stays loaded until Firefox restarts.
- **Release:** upload `dist/puzzle-chat-firefox-<version>.zip` to addons.mozilla.org as an
  **unlisted** add-on (Mozilla signs it; it is not searchable), then share the signed `.xpi`
  link with the team. Notes for the submission form: `store/amo-notes.md`.
- **Server:** nothing to configure. Firefox gives every install its own random
  `moz-extension://<uuid>` origin, so it cannot be listed; the server accepts any well-formed
  one instead (a web page can never send that origin). `web.extension_origins` is for
  Chrome only.
- **Differences from Chrome:** the toolbar button toggles the sidebar. Firefox only opens a
  sidebar from a click on the extension's own button, so **Work on this puzzle** and
  notification clicks switch to the tab but cannot open the sidebar; open it yourself.
  Firefox lets you revoke the Google Sheets permission in `about:addons`; the Options page
  then shows an **Allow access to Google Sheets** button.

## Options

- **Dashboard origin** - defaults to `https://quarantinedecrypters.com` (live). The test
  instance is `https://puzzbot.com`. Saving asks Chrome for permission to that origin; if you
  refuse, the origin is not saved. https only, no path.
- **Desktop notifications for new messages** - off by default.

## Server config: `web.extension_origins`

The live chat connection is a websocket, and the server checks its `Origin` header. The
extension's origin is `chrome-extension://<extension id>`, which is not the dashboard's
origin, so it must be listed explicitly in the `web` block of `config.json`:

```jsonc
"web": {
  // ...existing dashboard keys...
  "extension_origins": ["chrome-extension://abcdefghijklmnopabcdefghijklmnop"]
}
```

- Exact origins only; there is no wildcard. Default is `[]`.
- Without an entry the extension's live connection is refused (the panel cannot get live
  messages).
- Editing `config.json` needs a bot restart.
- Each distinct extension ID needs its own entry. For team installs, list the Chrome Web
  Store ID (below) rather than everyone's unpacked ID.
- Chrome only. Firefox origins (`moz-extension://<uuid>`) are accepted without an entry
  (see "Firefox" above).

## Using it

- Chrome cannot open the side panel by itself: click the extension icon. Once open (and pinned)
  the panel follows the active tab. A tab that is not a known puzzle sheet shows "not a puzzle".
- Tabs-mode hunts: only the chat panel applies (it follows the `#gid` of the sheet tab).
- Replies go through the bot, which posts via a "Puzzbot relay" webhook under your server
  name and avatar (Discord shows an APP tag). The bot needs **Manage Webhooks** in the puzzle
  channels.
- Dashboard buttons appear only when the extension is installed: "Work on this puzzle" (icon
  on the puzzle row, labelled button in the drawer), "Open puzzle as a tab group" (icon on
  the puzzle row, labelled button in the drawer; opens the puzzle's sheet and hunt page in a
  group named after the puzzle, or focuses that group if it is already open), an icon button
  in each round header ("Open round as a tab group", files-mode hunts only; reuses
  already-open tabs and moves them into the group) and "Close solved tabs" in the board
  header (files-mode only). Opening an already-open puzzle sheet switches to that tab and
  closes the duplicate unless it is the only tab in its window.
- The content script publishes the dashboard requests it relays (`data-puzzbot-actions` on the
  page, straight from its `ACTIONS` gate), and the dashboard shows each extension button only
  when its request is listed (`extensionSupports` in `frontend/src/lib/extension.ts`). A copy
  that publishes no list is 1.0.0 (`work`, `openRound`, `closeSolved`). So a new button can
  deploy before the extension update that handles it: add its action to `ACTIONS` and gate
  the button on it.
- Replies over 2000 characters, or from timed-out members, are refused.

Known limitation: your first panel reply may show the default Discord avatar in the
panel/dashboard transcript (Discord had not cached the avatar yet); later ones are fine.

## Packaging for the Chrome Web Store

1. Build the package from an explicit include list (manifest, `src/`, icons, `LICENSE-NOTICE.txt` and
   the repository's full `LICENSE`): `cd extension && bash package.sh` writes `dist/puzzle-chat-<version>.zip`.
2. Upload in the Chrome Web Store developer dashboard and publish as **unlisted** (installable
   by link only).
3. Copy the store-assigned extension ID, add `chrome-extension://<that id>` to
   `web.extension_origins`, and restart the bot.

## Tests

`npm install && npm test`.
