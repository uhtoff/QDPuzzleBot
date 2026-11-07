# Puzzle Chat extension

A Chrome (Manifest V3) extension that puts the Discord channel of the puzzle you are
looking at in a side panel next to the Google Sheet, and adds a few dashboard buttons for
opening puzzles. It talks to the puzzbot dashboard using your existing dashboard login; it
holds no Discord credentials of its own. Design: `docs/superpowers/specs/2026-11-07-puzzle-chat-extension-design.md`.
For the user-facing description see the "Chrome extension" section of `docs/user-guide.md`.

Requires Chrome 116 or later.

## Install (unpacked)

1. Open `chrome://extensions`, switch on **Developer mode**.
2. **Load unpacked** and pick this `extension/` folder.
3. Note the extension **ID** shown on its card. For an unpacked extension it depends on the
   folder path (unless a `"key"` is added to the manifest), so it differs between machines.
4. Add that ID to the server (see below), then restart the bot.
5. Open the extension's **Options** and check the dashboard origin.
6. Be logged in to the dashboard in the same Chrome profile (the extension reuses that session).

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

## Using it

- Chrome cannot open the side panel by itself: click the extension icon. Once open (and pinned)
  the panel follows the active tab. A tab that is not a known puzzle sheet shows "not a puzzle".
- Tabs-mode hunts: only the chat panel applies (it follows the `#gid` of the sheet tab).
- Replies go through the bot, which posts via a "Puzzbot relay" webhook under your server
  name and avatar (Discord shows an APP tag). The bot needs **Manage Webhooks** in the puzzle
  channels.
- Dashboard buttons ("Work on this puzzle", "Open round", "Close solved tabs") appear only
  when the extension is installed; see the user guide.

Known limitation: your first panel reply may show the default Discord avatar in the
panel/dashboard transcript (Discord had not cached the avatar yet); later ones are fine.

## Packaging for the Chrome Web Store

1. Zip the `extension/` folder **without** `node_modules/`, `test/` and `package*.json`:
   `cd extension && zip -r ../puzzle-chat.zip . -x 'node_modules/*' 'test/*' 'package*.json'`
2. Upload in the Chrome Web Store developer dashboard and publish as **unlisted** (installable
   by link only).
3. Copy the store-assigned extension ID, add `chrome-extension://<that id>` to
   `web.extension_origins`, and restart the bot.

## Tests

`npm install && npm test`.
