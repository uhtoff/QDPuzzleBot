# Firefox (addons.mozilla.org) submission notes

Upload `dist/puzzle-chat-firefox-<version>.zip` (built by `bash package.sh firefox`) as an
**unlisted** add-on. Mozilla signs it and returns an `.xpi` to share with the team by link.
The add-on id is `puzzle-chat@puzzbot.com` and the minimum version is Firefox 140; both are
set in `scripts/firefoxManifest.js`.

## Data collection declaration

The manifest declares these as **required** (`browser_specific_settings.gecko.data_collection_permissions`).
They mirror the Chrome Store disclosure in `listing.md` ("Data use disclosure"):

- **authenticationInfo** - the extension acts with the user's existing dashboard login session.
  The browser sends the cookies to the dashboard; the extension cannot read them and stores
  no credentials.
- **websiteContent** - the Google Sheets spreadsheet id and sheet (gid) of open Sheets tabs are
  sent to the user's dashboard to find the matching puzzle.
- **personalCommunications** - the extension shows Discord messages received from the dashboard
  and sends messages the user types to it.

Nothing else is collected. Data goes only to the dashboard the user configured, and is not sold or
shared. All code is bundled (no remote code). Privacy policy: https://quarantinedecrypters.com/privacy

## Reviewer notes

Reuse `reviewer-notes.md` and `reviewer-setup-playbook.md`. On Firefox, the reviewer opens the
panel with the toolbar button (it is a sidebar), and the server needs no extension id
configured.

## Manual smoke test (desktop Firefox 140+)

Run against the test instance (`https://puzzbot.com`, staging) before uploading:

1. Load `dist/firefox/manifest.json` via `about:debugging`, set the dashboard origin in Options
   and accept the permission prompt.
2. The toolbar button toggles the sidebar open and shut (one click opens, the next closes).
3. The sidebar follows the active tab: a puzzle sheet shows its channel; another tab shows
   "not a puzzle".
4. History loads in the sidebar (the dashboard cookie is sent on the extension's requests).
5. A message posted in Discord appears live; a reply typed in the sidebar posts in Discord.
6. From the dashboard: **Work on this puzzle** (switches tabs, sidebar updates if open),
   **Open puzzle as a tab group**, **Open round as a tab group**, **Close solved tabs**.
7. Revoke "Access your data for docs.google.com" in `about:addons` -> Permissions; Options shows
   **Allow access to Google Sheets**, and clicking it restores puzzle detection.
8. **Close the sidebar** (an open sidebar may keep the background page awake), have a puzzle
   sheet open in a tab, and leave Firefox idle for 5+ minutes so the background page can be
   suspended. Then post in that puzzle's Discord channel: the unread badge should appear within
   about a minute.
9. With notifications on and the sidebar still closed, repeat step 8: the notification appears,
   and clicking it switches to the puzzle tab.
