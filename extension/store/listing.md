# Chrome Web Store listing (draft)

## Name
Puzzle Chat

## Short description (<=132 chars)
Chat with your team's Discord puzzle channel from the Google Sheet you are solving in.
(86 chars)

## Full description
Puzzle Chat adds a side panel to Chrome that shows the Discord channel for the puzzle in the Google Sheet you currently have open, so you can read and send messages without leaving the sheet.

It is a companion to the Puzzbot dashboard used by puzzle-hunt teams. You must have an account on a Puzzbot dashboard and be logged in to it in the same browser.

Features:
- Live chat for the puzzle in the active Google Sheets tab, in a side panel.
- Unread count on the toolbar icon.
- Optional desktop notifications for new messages (off by default).
- "Work on this puzzle" and "Open round" buttons on the dashboard open and group the right sheets for you.
- Optionally closes puzzle tabs once a puzzle is marked solved.

Puzzle Chat does not read the contents of your spreadsheets. It only looks at the address of Google Sheets tabs to work out which puzzle you are on.

## Single purpose
Show and send the team's Discord chat for the puzzle in the open Google Sheet, via the Puzzbot dashboard.

## Permission justifications
- sidePanel: displays the chat in Chrome's side panel next to the sheet.
- tabGroups: groups and colours the sheet tabs of a puzzle round when the user opens a round from the dashboard.
- storage: saves the user's settings (dashboard address, notification choice, group colours) and short-lived per-session state (which tab maps to which puzzle).
- scripting: registers a content script on the user's own dashboard origin only, so the dashboard's buttons can ask the extension to open or focus puzzle sheets.
- alarms: wakes the background worker to re-establish the live connection to the dashboard after a network drop and as a periodic watchdog.
- Host permission https://docs.google.com/spreadsheets/*: Chrome grants host permissions at the host level (paths are ignored), so the extension technically has access to docs.google.com. Its code only reads the address (URL) of Google Sheets tabs (paths under /spreadsheets/) to tell which puzzle each tab is, and nothing else on docs.google.com. No sheet content is read or modified.
- Optional permission "notifications": requested only when the user turns on desktop notifications in the options page.
- Optional host permission (dashboard origin): requested at runtime for the single dashboard address the user configures (default https://quarantinedecrypters.com), to call its API and open its live connection. Permission for a previously configured address is removed when the address is changed.

## Data use disclosure (Privacy practices tab)
- Collects: Authentication information -- the extension acts with the user's existing dashboard login session (cookies are sent by the browser to the dashboard; the extension cannot read them and stores no credentials).
- Collects: Website content (limited) -- the Google Sheets spreadsheet ID and sheet (gid) of the open Sheets tabs are sent to the user's dashboard to find the matching puzzle.
- Collects: Personal communications -- the extension displays Discord messages received from the dashboard and sends messages the user types to it.
- Live events: while signed in to the dashboard, the extension receives live message events for all puzzle channels the user can see on the dashboard. They are used only for chat display, unread counts and optional notifications, and are kept in memory / session storage only.
- Not collected: health, financial, location, web history beyond the Sheets IDs above, user activity such as keystrokes or mouse position.
- Certifications: data is not sold or transferred to third parties outside approved use cases; not used or transferred for purposes unrelated to the extension's single purpose; not used for creditworthiness or lending.
- Remote code: none. All code is bundled.

## Privacy policy URL
https://quarantinedecrypters.com/privacy

## Other fields to fill in
- Category: Productivity
- Language: English
- Screenshots (1280x800 or 640x400): side panel beside a sheet; options page. Provided by the user.
- Small promo tile 440x280 (optional).
- Support URL / homepage: https://github.com/uhtoff/QDPuzzleBot
