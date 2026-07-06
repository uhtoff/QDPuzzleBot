# Discord → Web message bridge (archiving as a byproduct)

**Date:** 2026-07-06
**Status:** Implemented on branch `feat/message-bridge` (Phases A–D complete; pending review/merge to staging). Ships dark behind `web.bridge_enabled` (default off). Live end-to-end smoke deferred (needs a running bot + DB + `archive_media_dir`); migration 027 not yet applied to any live DB.

## Goal

Mirror Discord messages from hunt puzzle channels into the web database as they
happen, render them in an in-dashboard chat viewer, and let hunt archiving
"come along for free" — an archived hunt's chat becomes a frozen, self-contained
web transcript instead of a dead Discord badge.

Today the Discord badge on the dashboard (`PuzzleLinks.tsx`) is a plain
`https://discord.com/channels/…` link. After `!archive_hunt` deletes the hunt's
channels, that URL points at a channel that no longer exists — a dead badge. The
existing archiver (`bot/core/hunt_archive.py`) replays human messages into a
Discord *thread* via a webhook and never persists anything to the DB. This design
adds a persistent web-side transcript so the badge can open a real chat window in
both live and archived states.

## Decisions (settled during brainstorming)

- **Capture model: live bridge.** Mirror messages to the DB as they arrive (not a
  backfill at archive time). Archiving then reduces to "the channel gets deleted;
  the transcript we already captured is the archive."
- **Bucket-safe by construction.** Capture is inbound-only: `on_message` /
  `on_raw_message_edit` / `on_raw_message_delete` are gateway *push* events — zero
  REST calls, zero Discord rate-limit buckets consumed. The old channel-creation
  bucket pain was an outbound-write issue and does not apply. The only Discord HTTP
  is attachment byte downloads from `cdn.discordapp.com` (a different host from the
  REST API), and those run on the background worker, off the gateway path.
- **What to mirror: everything, tagged.** Capture human messages, this bot's
  messages, other bots' messages, and `!`-commands — each row tagged with a `kind`.
  The viewer decides what to render; filtering is a reversible read-time choice.
- **Capture gate: known puzzle channels only.** Mirror a message only if its
  `channel_id` is one we already track in the `puzzles` table. Non-puzzle
  channels (admin, off-topic, private) are never written to the DB. This keeps the
  privacy surface aligned with what the board already exposes.
- **No backfill; keep the webhook-thread archiver.** The bridge is forward-only.
  Hunts (or messages) predating the bridge simply have no web transcript. The
  existing `hunt_archive.py` webhook-thread flow is left untouched as the
  Discord-native record.
- **Message delete = privacy purge; channel delete = archive (keep).** An explicit
  per-message deletion (`on_raw_message_delete`) hard-deletes the row, its
  attachment rows, and the attachment bytes on disk. Deleting a *channel* (what the
  archiver does at archive time) fires `on_guild_channel_delete`, **not** per-message
  delete events, so archived transcripts are preserved. Edits overwrite content
  (latest only, no edit history) for the same privacy reason.
- **Badge behavior: web viewer in both states.** The badge opens the in-dashboard
  chat window whether the hunt is live or archived. Live: real-time via the existing
  WebSocket, with a `discord://…` "open in Discord" link (app handoff, https
  fallback — the plain badge tends to open the browser, not the app, on Windows).
  Archived: frozen transcript, no Discord link.
- **Visibility: inherit board visibility.** Every message surface reuses the
  existing `auth.py` board-visibility predicate. If you can see the puzzle on the
  board, you can see its chat; otherwise 403. No new permission surface.

## Architecture

```
Discord gateway (push)
   on_message / on_raw_message_edit / on_raw_message_delete
        │
        ▼
  Bridge cog  ──enqueue──►  background worker (off event loop)
        │                        ├─ MessageDb.write         → messages / message_attachments
        │                        ├─ attachment downloader   → web.archive_media_dir (bytes on disk)
        │                        └─ events.publish({type:"message"|"message_edit"|"message_delete"})
        │                                                        │
        ▼                                                        ▼
   (no REST, no buckets)                            /api/ws/activity (existing WebSocket)
                                                                 │
   GET /api/channels/{id}/messages  ◄── React ChatViewer modal ─┘
   (paginated, auth-gated)                (live append while open)
```

Reused infrastructure: the off-loop worker pattern from `bot/cogs/activity.py`,
the pub/sub in `bot/web/events.py`, the `/api/ws/activity` WebSocket
(`bot/web/api.py:290`), and the visibility check in `bot/web/auth.py`. New
machinery: the `messages` store, the attachment downloader, and the viewer UI.

## Data model

New migration (next free number — **collision-check before merge**, per the
parallel-worktree gotcha).

### `messages`

| column | type | notes |
|---|---|---|
| `id` | BIGINT UNSIGNED PK | Discord message snowflake — globally unique and time-sortable, doubles as the ordering key |
| `channel_id` | BIGINT UNSIGNED | indexed; transcript key |
| `guild_id` | BIGINT UNSIGNED | indexed; used by the visibility filter |
| `author_id` | BIGINT UNSIGNED | |
| `author_name` | VARCHAR(255) | display name at send time (frozen; survives nick changes) |
| `author_avatar` | VARCHAR(255) NULL | avatar URL for rendering |
| `kind` | TINYINT | human / this-bot / other-bot / command |
| `content` | TEXT | message body |
| `reference_id` | BIGINT UNSIGNED NULL | replied-to message id, for reply rendering |
| `created_at` | DATETIME(3) | from `message.created_at` |
| `edited_at` | DATETIME(3) NULL | set by edit handler |

Index `(channel_id, id)` for cursor pagination
(`WHERE channel_id=? AND id < ? ORDER BY id DESC LIMIT n`).

Note: no `deleted_at`. Deletes are **hard** (privacy) — see decisions.

### `message_attachments`

| column | type | notes |
|---|---|---|
| `id` | BIGINT UNSIGNED PK | attachment snowflake |
| `message_id` | BIGINT UNSIGNED | indexed |
| `filename` | VARCHAR(255) | original name |
| `content_type` | VARCHAR(100) NULL | |
| `size` | INT | bytes |
| `source_url` | VARCHAR(512) | Discord CDN URL — renders before the channel is deleted; fallback |
| `local_path` | VARCHAR(512) NULL | saved bytes; NULL until downloaded / on failure |
| `download_status` | TINYINT | pending / done / failed |

Bytes stored under `web.archive_media_dir` laid out
`…/{guild_id}/{channel_id}/{attachment_id}_{filename}`, served through an
auth-gated Quart route (not raw nginx) so the same visibility check governs images
as governs the transcript. X-Accel-Redirect handoff to nginx is a future
optimization, not v1.

## Phases (one spec, four independently verifiable slices)

### Phase A — Capture & storage
- Migration for `messages` + `message_attachments`.
- `MessageDb` accessor in `bot/store/mysqldb.py` (`_MySQLBaseDb` pattern):
  `write`, `mark_edited`, `delete` (row + attachment rows), `add_attachment`,
  `set_attachment_local`. Isolated, unit-testable against existing store test infra.
- Bridge cog `bot/cogs/bridge.py` (mirrors `activity.py`'s worker):
  - `on_message` → build record `(id, channel_id, guild_id, author, kind, content,
    reference_id, created_at, attachments[])`, gate on known-puzzle-channel set,
    enqueue. Returns immediately. `kind`: `author.id == application_id` → this-bot;
    other `author.bot` → other-bot; content starts with `!` → command; else human.
  - `on_raw_message_edit` / `on_raw_message_delete` → enqueue edit/delete ops by id.
    **Raw** variants are required: non-raw fire only for cached messages, so older
    messages would miss edits/deletes.
  - Background worker drains off the event loop (like `ChannelActivityDb._drain`):
    writes via `MessageDb`, then downloads each attachment (bounded timeout + size
    cap, bot aiohttp session) to disk and updates `local_path`/`download_status`.
    Download failure leaves `source_url` intact so it still renders pre-delete.
- Known-puzzle-channel set: in-memory, seeded from `puzzles.channel_id`, refreshed
  on puzzle create/delete.
- Gated behind `web.bridge.enabled` (default **off**) — ships dark.
- **Verifiable alone:** flip flag on, send messages, watch rows + files appear;
  edit/delete and see the row change/vanish.

### Phase B — Read API & viewer
- `data.py`: `get_channel_messages(channel_id, before=None, limit=50)` — cursor
  pagination by snowflake id, joins attachments; authorizes via the same predicate
  `build_board` uses (honors per-hunt/round visibility, not just guild membership).
- Routes: `GET /api/channels/{channel_id}/messages?before=<id>` (403 for
  non-viewers); `GET /api/attachments/{id}` for bytes (same check).
- React `ChatViewer` modal (Mantine), opened from `PuzzleLinks`: Discord-style
  render — author name + avatar, timestamp, content, inline images / file links,
  reply quotes, "*(edited)*" marker; infinite scroll upward for history.

### Phase C — Live push
- Worker publishes `{type:"message"|"message_edit"|"message_delete", channel_id,
  guild_id, …}` after each DB write.
- `/api/ws/activity` carries them; extend `_board_event_visible` (`api.py:281`) to
  message events (same guild/visibility filter).
- Open `ChatViewer` for a channel appends/updates/removes live via
  `useActivityFeed`. Archived channels emit no new events → viewer is naturally
  static.

### Phase D — Badge swap & archive integration
- `PuzzleLinks` badge becomes a button opening `ChatViewer` in both states.
- Live hunts: `discord://…` "open in Discord" link inside the modal (https
  fallback). Archived hunts: frozen transcript, no Discord link.
- `hunt_archive.py` and the `archive_hunt` service are **untouched** — the web
  archive is simply what the bridge already captured. Channel deletion at archive
  time does not purge messages (no per-message delete events fire).

## Visibility

Transcript route, attachment route, and WS message events all reuse the existing
board-visibility check in `bot/web/auth.py`. No new permission surface.

## Config & rollout

- `web.bridge.enabled` (default off) — gates the whole capture path.
- `web.archive_media_dir` — attachment byte storage root.
- Attachment `max_bytes` + allowed content-type policy.
- One new migration, collision-checked before merge.

## Testing

- `MessageDb`: write / edit / hard-delete (row + attachment purge) via store test infra.
- Capture: mock `Message`, assert `kind` tagging; gate drops non-puzzle channels;
  worker enqueues (AsyncMock for awaited fetches per house rules).
- Attachment: mock download success/failure; delete purges bytes on disk.
- Route: pagination + 403 for non-viewer.
- WS: message-event visibility filtering.
- Frontend: viewer renders transcript, live append, edited/deleted handling.

## Out of scope

- Backfilling pre-bridge history.
- Retiring or changing the webhook-thread archiver.
- Reaction mirroring (the webhook archiver already skips reactions; not added here).
- Editing/deleting messages *from* the web dashboard (read-only viewer).
- nginx X-Accel-Redirect media serving (future optimization).

## Key file references

- `bot/core/hunt_archive.py` — existing webhook-thread archiver (untouched).
- `bot/cogs/activity.py:49` — `on_message` + off-loop worker pattern to mirror.
- `bot/web/events.py` — pub/sub. `bot/web/notifier.py:15` — `WebNotifier`.
- `bot/web/api.py:281,290` — `_board_event_visible`, `ws_activity`.
- `bot/web/data.py:205` — where `discord_url` is built; read-model home.
- `bot/web/auth.py` — visibility check to reuse.
- `bot/store/mysqldb.py:630` — `ChannelActivityDb` as the `MessageDb` template.
- `frontend/src/components/PuzzleLinks.tsx:31` — Discord badge to convert.
- `frontend/src/hooks/useActivityFeed.ts` — WS consumer to extend.
