# Notes for Chrome Web Store reviewers (draft)

> **TODO BEFORE SUBMISSION: every `<ANGLE-BRACKET PLACEHOLDER>` below (test account section) must be filled in by the repository owner. Do not submit with placeholders.**

Puzzle Chat is a companion to a self-hosted web app (the Puzzbot dashboard) used by puzzle-hunt teams. It does nothing useful without an account on that dashboard, so a test account is provided.

## Test account (USER MUST PROVIDE)
- Dashboard URL: <TEST DASHBOARD ORIGIN, e.g. https://example.com>
- Username: <TEST USERNAME / login method>
- Password / login instructions: <TEST CREDENTIALS>
- Discord server invite (if reviewers need to post): <TEST SERVER INVITE>
- A test hunt with at least one puzzle whose Google Sheet is viewable by the link: <SHEET URL>
- The test account must be logged in to Discord-linked dashboard features as needed: <NOTES>

## Steps
1. Install the extension. Open its options page (right-click the toolbar icon, Options).
2. Enter the dashboard URL above and press Save. Accept the host-permission prompt (this is the optional dashboard-origin permission).
3. Log in to the dashboard in a normal tab using the test account.
4. Open the test puzzle's Google Sheet (link above) in a tab. The sheet must open at docs.google.com/spreadsheets/.
5. Click the Puzzle Chat toolbar icon to open the side panel. It shows the puzzle's chat. Type a message and send it; it appears in the panel and in the linked Discord channel.
6. Optional: on the options page tick "Desktop notifications"; Chrome asks for the optional notifications permission. Send a message from Discord to see a notification.
7. Optional: on the dashboard, use "Work on this puzzle" on a puzzle: the extension opens or focuses the sheet tab.

## Notes
- The extension reads only the address (URL) of Google Sheets tabs (via the docs.google.com/spreadsheets host permission); it never reads sheet contents.
- If the side panel shows "log in", log in to the dashboard in the same browser profile.
- No remote code is loaded; all scripts are in the package.
