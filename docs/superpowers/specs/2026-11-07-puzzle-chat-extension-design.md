# Puzzle chat extension — design

Date: 2026-11-07. Status: draft for review.

## Intent

Mid-hunt, picking up a puzzle means arranging Discord, the dashboard and the puzzle's
Google Sheet by hand, and chat in a puzzle's Discord channel is easy to miss while
heads-down in the sheet. Jolly Roger shows chat beside the puzzle; we want that, without
leaving Google Sheets or Discord.

A Chrome extension that puts a puzzle's Discord channel in Chrome's side panel next to
that puzzle's sheet, lets you read and reply there, and makes opening/closing puzzle
tabs a one-click job.

Users: the user first, then the whole team (so it must install cleanly for others in the
end — e.g. an unlisted Chrome Web Store listing — not stay a developer-mode build).

## Goals

1. **Chat side panel.** When the active tab is a known puzzle sheet, the extension's side
   panel shows that puzzle's Discord channel: recent messages, live updates, and a box to
   reply. Replies land in the real Discord channel under the replier's Discord name and
   avatar (posted by the bot via a channel webhook; Discord shows an "APP" tag).
   - Pinned: the panel stays open and follows the active tab — another puzzle's sheet
     shows that puzzle's chat; a non-puzzle tab shows a neutral "not a puzzle" state.
   - Closed: unread messages for open puzzle tabs show as a count on the extension icon
     (and an optional desktop notification, off by default). Clicking opens the panel on
     that puzzle.
2. **"Work on this puzzle"** from the dashboard (puzzle drawer / board row): open the
   puzzle's sheet — or switch to its tab if already open — and its chat in the panel.
3. **Reuse open tabs:** navigating to a known puzzle sheet that is already open in another
   tab (e.g. clicking the sheet link in the puzzle's Discord pin, in web or desktop
   Discord via the browser) switches to the existing tab instead of duplicating it.
4. **Open round:** from the dashboard, open every puzzle sheet in a round, collected into
   one Chrome tab group named after the round. This is what gives files mode the old
   one-spreadsheet-with-tabs feel (user, 2026-11-07).
5. **Close solved:** one action closes every open tab whose puzzle is solved.

## Non-goals (this version)

- Full Discord in a split view, and any split ratio (Chrome 155's Split View API can
  create splits but cannot size them; the panel replaces the need).
- The panel opening itself on a new message (Chrome requires a user gesture to open the
  side panel; we nudge instead).
- Images, reactions, threads, editing/deleting, mentions autocomplete in the panel —
  plain-text read + reply only; real Discord remains for the rest.
- "Work on this with voice" — later, once puzzles have voice channels (voice is a
  separate, parked decision in docs/PLANS.md).
- Firefox/Safari.

## Anti-goals

- **Breaking anything that already works.** All changes are additive: new extension, new
  API routes, an origin allow-list entry. The dashboard, message bridge, bang commands and
  existing routes behave exactly as today. No schema changes beyond what the new reply
  route strictly needs (ideally none).
- A reply path that lets someone post as somebody else, or post into a channel they
  can't see on the dashboard.

## Constraints and existing pieces

- Puzzle data and URLs come from the dashboard API (Quart, `bot/web/`), signed in with
  the user's existing dashboard session (Discord OAuth). Session cookie is HttpOnly,
  `SameSite=Lax`; state-changing calls require a double-submit CSRF token (header must
  match a JS-readable cookie — see `bot/web/__init__.py`).
- Messages: `GET /api/channels/<id>/messages` (visibility-checked against the user's
  membership/roles) already serves the bridge's stored messages; the bridge publishes new
  messages onto the activity event bus, which `/api/ws/activity` streams.
- `/api/ws/activity` rejects any Origin other than the dashboard's
  (`_ws_origin_allowed`); the extension's `chrome-extension://<id>` origin must be added
  as an explicit, configured allow-list entry — never "any origin".
- The message bridge is one global setting (`web.bridge_enabled`, read at bridge cog
  load), not per hunt. The API exposes it so the panel can say "chat unavailable — open in
  Discord" when it is off.
- Posting: the bot already uses webhooks (hub archive needs Manage Webhooks), so per-
  channel webhooks are an established pattern. A webhook bypasses Discord's own
  permissions, so the reply route enforces them itself (designer's call): the user must
  pass the messages route's hunt-visibility check AND, live, have `view_channel` and
  `send_messages` on that channel as a guild member (not timed out); replies are refused
  for archived hunts and channels that no longer exist. Posts use the user's own server
  display name/avatar only, with mentions disabled (`allowed_mentions` none) except
  replies to nobody.
- Bang commands: a webhook message is a bot message, which bang commands ignore, so a
  `!command` sent from the panel would silently do nothing. The panel refuses text
  starting with `!` and says to run commands in Discord (designer's call).
- Sheet URLs: tabs mode (one spreadsheet, a tab per puzzle — URL differs only by
  `#gid=`) and files mode (a spreadsheet per puzzle). Matching a tab to a puzzle must
  handle both; `bot/core/sheet_location.py`'s `puzzle_location` is the existing URL
  source.
- Two instances (live/test) on different hostnames; the extension needs a configurable
  dashboard URL (options page), defaulting to live.
- Chrome 155+ (Split View not used, so this is only a floor for the tab APIs we do use —
  the builder sets the real minimum).

## Decided

- Chrome extension, Manifest V3, Chrome only.
- Chat lives in Chrome's side panel (user, 2026-11-07); nothing else shares the screen
  with the sheet initially.
- Replies are sent by the bot through a webhook, under the user's name/avatar.
- Entry points: the dashboard first; Discord sheet links via tab reuse.
- Lives in this repo.

## Files mode first; tabs mode gets the panel only

Files mode (a spreadsheet per puzzle) is the target: there, one Chrome tab is one puzzle,
and goals 2–5 apply as written (user, 2026-11-07: the round tab group exists to give
files mode the tabbed-spreadsheet feel). In tabs mode (one spreadsheet, puzzles differ
only by `#gid=`) a Chrome tab can show several puzzles over time, so only the chat panel
applies: it follows the puzzle of the gid currently showing, including gid changes within
one tab. "Work on this puzzle" in tabs mode just opens the sheet at that gid; tab reuse,
open round and close solved are files-mode only (designer's call — the cheapest
behaviour that doesn't fight the one-spreadsheet model).

## Tab matching (designer's call)

The extension remembers which puzzle it matched each tab to, keyed by tab id, rather
than re-deriving it from the URL every time. That covers a tabs-mode solve, which moves
the puzzle's sheet to the solved spreadsheet with a new id and gid, so an open tab's
old URL no longer matches the puzzle's current location. No server-side history of past
locations, so no schema change.

## Left to the builder

- How the dashboard page talks to the extension (e.g. `externally_connectable` messaging
  vs a dashboard-side "open in extension" link), and how the dashboard detects the
  extension is installed (hide the buttons otherwise).
- Auth mechanics from the extension (cookies via host permissions vs a token the
  dashboard hands the extension), provided it reuses the dashboard login and the CSRF
  rule still guards state changes.
- Panel UI stack (vanilla vs reusing the frontend's React/Mantine), build tooling,
  directory layout, packaging for the Web Store.
- Webhook lifecycle (create on first reply, cache id, recreate if deleted).
- Unread-count bookkeeping and notification wording.
