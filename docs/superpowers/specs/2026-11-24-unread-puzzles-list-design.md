# Unread puzzles list in the chat extension — design

Date: 2026-11-24. Branch: `unread-list` (from `staging`).
Mockup (interactive, the agreed look and behaviour):
`docs/superpowers/specs/2026-11-24-unread-puzzles-list-mockup.html`
(open it in a browser; where this document and the mockup disagree, this document wins).

## Intent

During a hunt a solver sits in one puzzle's sheet with that puzzle's chat in the side
panel. They cannot see that other puzzles are getting messages, or that something was
just solved, without leaving for Discord. Put a short list of "puzzles with new messages"
at the top of the panel, let them look at any of those chats without leaving their sheet,
and make a solve impossible to miss.

## Goals

- A list of the current hunt's chats that have new messages, most recent first, five per
  page, between the panel header and the chat.
- The list is folded by default and remembers whether the user opened it.
- Look at another puzzle's chat in the panel without changing browser tab, and get back.
- A puzzle being solved shows as a green row with its answer.
- Unread state survives browser restarts, laptop sleep and socket reconnects.
- Works in Chrome and Firefox.

## Non-goals

- Read state shared across browsers or devices (rejected: niche, and a lot of storage).
- Any link to Discord's own read state. Bots and OAuth cannot read or set it; doing it
  with the user's personal Discord token is against Discord's terms. Never do this.
- Unread marks on the web dashboard.
- A list spanning several hunts at once.
- Changes to desktop notifications.

## Anti-goals

- The folded list taking more than one thin strip away from the chat.
- The panel getting stuck on a peeked chat after the user switches browser tab.
- Installing or upgrading and being shown hundreds of old messages as new.
- A second, different notion of "unread" living beside the toolbar badge's.

## Constraints

- The extension ships through the Chrome Web Store and a self-hosted Firefox add-on, and
  can reach users before or after the dashboard it talks to is updated. If the new
  endpoint is missing (404) or fails, the list is simply hidden; nothing else changes.
- No new extension permissions.
- No database migration.
- Panel text is built as DOM nodes, never `innerHTML` (existing rule in `panel.js`).
- The hunt general channel chat exists only on `staging` (`6cb921b`, `6faee3c`); this work
  builds on `staging`.

## Approaches considered

1. **Extension only, live counts.** Count the live `message` events the extension already
   receives for every visible hunt and currently discards. No server change, but the list
   is empty after every browser start and misses anything said while the laptop slept or
   the socket was down.
2. **Extension keeps read markers; server answers "what's new since these".** One new
   read-only endpoint per hunt. Survives restarts and gaps, no database change; read
   state is per browser. **Chosen**, plus the rule that posting in a channel from Discord
   counts as having read it.
3. **Server stores read state per user.** Syncs across browsers, but needs a migration and
   a write per chat opened. Rejected by the user as niche and storage-heavy.
4. **Mark as read on Discord.** Not possible without the user's personal token. Rejected.

## How it works

### What "unread" means

A read marker is a Discord message id (snowflakes sort by time). The extension stores, in
`chrome.storage.local`, per dashboard origin:

- `baseline`: one marker that applies to every channel. Set to "now" (a snowflake built
  from the current time) the first time the feature runs, so nothing starts unread.
- `read`: `{channel_id: message_id}` for channels read more recently than `baseline`.

A channel's effective marker is the larger of `baseline`, its `read` entry, and (applied
by the server) the id of the user's own most recent message in that channel.

- **Unread count** = messages in the channel with id above the effective marker, of kind
  human (`KIND_HUMAN`). Bot posts and `!` commands (`KIND_COMMAND`) do not count.
- **Newly solved** = the puzzle is solved and its `solve_time` is after the marker's time.
- **Looking at a chat** in an open panel sets its `read` entry to the larger of that
  chat's newest message id and a snowflake for "now" (client clock, as `baseline` uses),
  and keeps doing so as messages arrive while it stays visible. Using "now" is what
  clears a solved row when no message followed the solve.
- **Mark all read** sets `baseline` to now and empties `read`. The previous
  `{baseline, read}` is kept for 8 seconds for Undo.
- Entries at or below `baseline` are pruned.

### Server: one new endpoint

`POST /api/hunts/<uid>/unread`, body `{"baseline": "<id>", "read": {"<channel_id>": "<id>"}}`.
Read-only; it is a POST only because the body can exceed a query string. It goes through
the same login, hunt-visibility (`_load_visible_hunt`) and CSRF path as the extension's
existing POST.

Response: one entry for the hunt's general channel and one per top-level puzzle in the
hunt, solved or not:

    {"channels": [{
      "channel_id": "…", "kind": "puzzle" | "general",
      "name": "…", "rounds": ["…"],
      "solved": false, "solution": null, "newly_solved": false,
      "sheet_url": "…" | null,
      "unread": 3,
      "last": {"id": "…", "author_name": "…", "content": "…", "created_at": "…"} | null
    }]}

`last` is the newest human message. Every channel is returned, including `unread: 0` and
long-solved ones, so the extension knows every channel's name and round, can update
itself from live events, and can still badge a solved puzzle whose tab is open. What the
list shows is decided in the extension: unsolved with `unread > 0`, or `newly_solved`.
The user's own-last-message rule is applied here, using the logged-in user's Discord id
against stored `author_id`.

Mini-puzzles (`parent_puzzle_id` set) get no entry. They live in a thread of their
minimeta's channel; their chat is out of scope for this version.

### Extension: background

- Owns the markers and a per-hunt cache of the endpoint's response.
- Fetches for a hunt when: a panel first shows a chat in that hunt, the socket
  (re)connects, the service worker wakes, a `board` event arrives for that hunt (this is
  how solves and new puzzles are learned), or a `message` event arrives for a channel not
  in any cached hunt. Events carry no hunt id, so that last case refetches the hunts that
  open panels are showing, and a channel still unknown afterwards is remembered as a miss
  and ignored until the next fetch for another reason.
- Otherwise applies live `message` events to the cache itself: bump `unread` and replace
  `last` for a human message in a non-visible channel; clear the channel if the author is
  the user (when the extension can tell) or the channel is visible.
- Sends each panel its list for the hunt of the chat that panel is showing. A panel
  showing no puzzle keeps the last hunt it showed; if there has never been one, no list.
- The toolbar badge keeps its current meaning (unread in puzzles the user has tabs open
  for) but takes its numbers from this same store, replacing `lib/unread.js` counts. It
  therefore counts human messages only; today it also counts bot posts and commands.
- Archived hunts get no list.

### Extension: panel

Layout top to bottom: header, peek bar (only while peeking), unread list, chat, composer.

**List** (see mockup for exact look):

- Folded: one strip, "New in N puzzles · M", plus a green "✓ K solved" chip when any row
  is newly solved. The totals pulse once when something arrives. Hidden entirely when
  there is nothing unread.
- Open: up to five rows, each with count, puzzle name, round, age, and a second line with
  "Author: last message" (mentions and emoji rewritten as the chat does, one line,
  ellipsised). Footer: "✓ Mark all read" on the left, `‹ 1–5 of 12 ›` on the right when
  there is more than one page.
- On a multi-page list the last page is padded to five rows so the chat does not jump.
- Order: most recent activity first (newest of last message and solve). A new arrival
  returns the list to page 1.
- The chat the panel is showing never appears in the list.
- Fold state is saved in `chrome.storage.local`. Default folded.
- **Solved row:** green background and left edge, a tick instead of the count, and
  "Solved: ANSWER" instead of the last message. It stays until looked at or marked read,
  then the puzzle leaves the list for good. If the puzzle is un-solved it is an ordinary
  row again.
- After Mark all read, the list is replaced by "Marked N chats read · Undo" for 8 seconds.

**Row actions:**

- Click: peek. The panel shows that chat; the browser tab does not change.
- Ctrl-click, ⌘-click, middle-click, or the ↗ button on the row: go to the puzzle.
  If a tab for it is already open, activate it; otherwise open its sheet in a new tab
  using the existing open-puzzle path. The general chat goes to the hunt board on the
  dashboard. A puzzle with no sheet goes to the hunt board too.

**Peeking:**

- A peek is a per-panel override of "show the active tab's chat". It ends when the user
  activates a different tab, presses Back, or goes to the puzzle.
- While the panel's chat differs from the tab's, a bar under the header shows
  "Go to puzzle ↗" and "← Back to <tab's puzzle>". Back carries an unread count if the
  tab's own puzzle got messages meanwhile; while peeking, the tab's puzzle can appear in
  the list like any other.
- Replying while peeking posts to the peeked chat (the header names it).
- When the chat being shown is solved, the header shows "✓ Solved · ANSWER".

## Testing

- Server: pytest for the endpoint — counts respect the marker, `baseline`, own-message
  rule and message kind; hidden hunts 404; solved-before-marker puzzles are omitted and
  newly solved ones included; general channel present. Use at least two channels and two
  markers per property so a swapped or ignored marker fails.
- Extension: marker maths, cache updates from events, and list ordering/pagination go in
  pure modules under `extension/src/lib/` with tests in `extension/test/`, like the
  existing modules.
- Panel DOM and background wiring have no test runner: verify by hand in Chrome and
  Firefox and say so in the report rather than implying they are covered.

## Left to the builder

- Module names and how the cache is shared with panels (existing port messages).
- Exact SQL; one query per hunt, not one per channel.
- Whether the extension can know the user's Discord id for the live "my own message"
  shortcut. If not, the server-side rule alone is enough; it applies at the next fetch.
- Whether replies sent through the extension's relay webhook are stored with the real
  user's id. If not, nothing breaks: the user was looking at that chat.
- Debounce of refetches under a burst of `board` events.
- Version bump (1.2.0) and store notes.

## Calls made by Claude, not explicitly decided by the user

- Markers are message ids with a single `baseline`, rather than timestamps.
- The endpoint is a POST.
- The toolbar badge keeps its open-tabs-only meaning, and becomes human-messages-only.
- Mini-puzzle threads are not listed or counted.
- "Looked at" is stamped with the browser's clock.
- Solved puzzles leave the list permanently once seen, even if chat continues there.
- A new arrival returns the list to page 1 (shown in the mockup, not discussed).
- "Go to" for a puzzle with no sheet, and for general, opens the hunt board.
- The list is hidden rather than showing an error when the endpoint is unavailable.
