# Bridge capture-behavior follow-ups — design

Follow-ups to the Discord→web message bridge, from live testing. Three interrelated
capture-behavior fixes. Rendering polish (reply-quote render, download filename,
shared WS) is deferred to a later pass.

## Observations → root causes

1. **~60s delay before a new channel starts capturing.** The puzzle-channel gate
   (`_puzzle_channels`) is a snapshot refreshed only every 60s (`bridge.py`
   `_REFRESH_SECONDS`). Messages before the next tick are dropped at the gate.
2. **Welcome message never appears (desired).** Happy accident of (1): the
   welcome/info embed is sent at channel creation, before the refresh tracks the
   channel. Not a deliberate filter.
3. **Bot solve messages show as empty rows.** Solve/partial/unsolve announcements
   are `discord.Embed` with no text `content` (`puzzles.py`). The bridge captures
   only `message.content`; embeds are dropped, so the row exists but is blank.

Create ordering (confirmed, `puzzle_service.create_puzzle`): channel created →
`puzzles.channel_id` row committed → `notifier.welcome()` sends+pins the welcome.
So the welcome is always the **first post to a brand-new puzzle channel**, and the
row exists before it is sent (prompt-tracking would capture it — hence we must
explicitly exclude it).

## Fixes

### #1 Prompt channel tracking
Keep the 60s positive-set refresh (catches channel→puzzle conversions). Add a lazy
check: on an untracked channel in `on_message`, do one DB lookup — is it a puzzle
channel? Cache the answer (positive → `_puzzle_channels`; negative →
`_non_puzzle_channels` so busy general channels aren't re-queried). Positive set is
checked first, so a later conversion picked up by the refresh always wins over a
stale negative. New channels capture from their first human message.

### #2 Welcome exclusion
Skip an insert when **all** hold: the channel has **no stored messages yet**
(`MessageDb.channel_has_messages` == False), the author is a **bot**
(`kind in {THIS_BOT, OTHER_BOT}`), and the message is **embed-only** (empty text
`content`). Backed by an in-memory `_seen_channels` set so skipping the welcome
(which leaves the DB empty) doesn't re-trigger on the next bot embed. The DB-empty
check makes it robust across restarts; the bot+embed-only guard means a human's
first message is never swallowed on rollout. Effectively: swallow the first welcome
embed in a brand-new channel, nothing else.

### #3 Embed capture
- **Migration 028**: add nullable `embeds` TEXT column to `messages` (JSON payload).
- **Capture**: normalize `message.embeds` to a stable subset —
  `{title, description, url, color, fields:[{name,value,inline}], footer}` — and
  store as JSON. `on_message` uses `embed.to_dict()`; `on_raw_message_edit` uses the
  raw `payload.data["embeds"]`. Both go through one `_normalize_embeds` helper.
- **Persist**: `MessageDb.write` gains an `embeds` param; `mark_edited` also updates
  `embeds` (edits can change them).
- **Serve**: `get_channel_messages` selects+deserializes `embeds`; the live
  `_message_payload` includes them (currently omits embeds, hardcodes attachments=[]).
- **Render**: `ChatViewer` draws a Discord-style embed block (colored left border,
  title, description, fields) from a new `ChatEmbed` type.

## Out of scope (deferred)
Reply-quote rendering (#4), Content-Disposition filename for non-image downloads
(#5), viewer sharing the board WS feed (#6).

## Delivery
Three reviewable commits: embed capture (#3, the bulk), prompt-tracking (#1),
welcome-exclusion (#2). Tests alongside each. Verify: pytest (bridge/message
suites), basedpyright, ruff, frontend tsc.
