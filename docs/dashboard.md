# Puzzle Dashboard (Phase 1)

A React/Mantine SPA over a Quart JSON API embedded in the bot process.

Read: hunt lists grouped by guild (reverse-chronological), puzzles grouped by
meta, solutions, a filterable puzzle list, evidence, and live Discord
activity.
Write (organiser-gated except notes/evidence): notes, retag puzzles between
metas (drag board + add/remove menu), create puzzles and metas, solve/unsolve,
sheets, clues/evidence, minimeta and convert-to-* type changes, rename,
tag-group delete/rename, reconcile, and rollup rebuild — roughly 36 write
routes in total. **Admin-gated** (a strictly narrower set of role names) are
the hunt-lifecycle routes only: hunt create, settings, archive, solved-sheet,
gate/re-gate behind a team role, and hunt delete. The convert-to-* routes are
deliberately organiser, matching the group-keyed change-type route — they were
admin until 2026-08-08, which an organiser could bypass by addressing the group
instead of the puzzle. Write-actions
run through ctx-free `web_*` methods on the Puzzles cog (mirrored, for
Discord-visible side effects, by `bot/core/notifier.py`), so the website
creates real Discord channels + Google sheets and posts/refreshes the same
messages and pins as the matching `!` commands.

## Architecture

- `bot/web/` — Quart app started from `setup_hook` (only when `web.enabled`).
  - `auth.py` — Discord OAuth (`identify` scope) → httpOnly session cookie.
    Membership and team roles come from the live bot, not OAuth scopes.
  - `api.py` — read endpoints and the live-activity WebSocket:
    `/api/guilds`, `/api/hunts`, `/api/hunts/<uid>`, `/api/hunts/<uid>/puzzles`,
    `/api/rollups/<id>/answers`, `/api/evidence` (GET+POST), and
    `/api/ws/activity`.
  - `actions.py` — the write routes listed above (solve, retag, convert-to-*,
    hunt lifecycle, reconcile, evidence edit/delete, etc.), each backed by a
    `PuzzleService`/cog `web_*` call.
  - `data.py` — dashboard read queries on a dedicated connection pool, run off
    the event loop via `asyncio.to_thread` (never shares the bot's connection).
  - `events.py` — in-process pub/sub feeding the activity WebSocket.
- `bot/core/notifier.py` — ctx-free Discord notifications (welcome messages,
  pin refreshes, solved embeds) shared by bang commands and web actions, so
  both callers produce the same Discord-visible output.
- `bot/cogs/activity.py` — records the last human message per channel into
  `channel_activity` and publishes a live event.
- `frontend/` — Vite + React + TS + Mantine SPA.

## Message bridge (chat viewer)

A forward-only mirror of hunt puzzle-channel messages into the web DB, so the dashboard can
show each puzzle's Discord discussion in an in-app **chat viewer** — and an archived hunt
keeps a self-contained transcript after its Discord channels are gone.

- `bot/cogs/bridge.py` — the `MessageBridge` cog. Captures messages from puzzle channels via
  gateway push events only (no REST calls, no rate-limit buckets), doing all DB writes and
  attachment downloads on a background worker off the gateway path (mirrors
  `bot/cogs/activity.py`). Lazily gates on puzzle channels (per-channel DB check + negative
  cache), skips the initial welcome/info embed, and publishes live message/edit/delete events.
- `bot/store` `MessageDb` — DAO for the `messages` / `message_attachments` tables and the
  embeds column (migrations `027_message_bridge.sql`, `028_message_embeds.sql`).
- `bot/core/message_media.py` — safe-filename + size-capped attachment downloader; bytes land
  under `web.archive_media_dir`. `bot/core/media_store.py` (image evidence — `!clue`
  attachments, dashboard uploads) writes to the same directory and is independent of
  `bridge_enabled`; see the config note below.
- `bot/web/api.py` — read routes `/api/channels/<id>/messages` (transcript, behind the same
  hunt-visibility gate) and `/api/attachments/<id>` (serves stored bytes — images inline,
  other files as downloads). Live events fan out over the existing `/api/ws/activity` socket.
- `frontend/src/components/ChatViewer.tsx` — a Discord-style modal (avatars, replies, images,
  embeds, `:shortcode:` emoji) opened from each puzzle row's Discord icon
  (`PuzzleLinks.tsx`): a live tail for active hunts, a static transcript for archived ones.

**Off by default** — capture ships dark. When disabled the cog stays inert (its listeners
no-op), the chat viewer simply has no history to show, and its "Open in Discord" button still
works. Capture is forward-only: it mirrors messages posted *after* it is enabled and does not
backfill channel history.

### Enabling the bridge

1. Apply the pending migrations (`python migrate.py`) — `027` and `028` add the message tables
   and the embeds column.
2. Add to the `web` block in `config.json`:
   ```jsonc
   "web": {
     // ...existing dashboard keys...
     "bridge_enabled": true,
     "archive_media_dir": "/var/lib/puzzbot/media",  // required (see below); a real, writable path outside the checkout
     "attachment_max_bytes": 26214400                 // optional; caps upload size, default 25 MB (see below)
   }
   ```
3. Restart the bot. Capture begins for puzzle channels as new messages arrive.

`archive_media_dir` is required whether or not `bridge_enabled` is true: image evidence
(`!clue` with an attachment, and the dashboard's evidence image upload) stores to the same
directory via `bot/core/media_store.py`, and refuses to store anything if it is unset — an
unset dir would otherwise resolve to the process's working directory (the checkout itself).
`attachment_max_bytes` caps both the message-bridge attachment size and evidence image
uploads; it also sets Quart's own `MAX_CONTENT_LENGTH` request-body guard, so raising it
raises both together.

## Hunt visibility

- **Active** hunts are visible only to holders of the hunt's team `role_id`
  (legacy hunts with `role_id = 0` fall back to any guild member).
- **Archived** hunts appear in the "Old hunts" menu, visible to any guild member.

See `memory/site-hunt-visibility.md` for the full model.

## Deploy steps (run when ready)

> These steps stand up a **single** instance. For the production model — a **live + test** bot side
> by side on one Plesk host under two system users (separate ports, DBs, subdomains and Discord
> apps) — follow [README § "Deploying on a Linux server"](../README.md#deploying-on-a-linux-server);
> it is the same steps run once per instance. The souwester/redhat reference below stays valid as
> the *different-boxes* (proxy on one box, bot on another) topology.

1. **Install deps** (additive; safe while the bot runs):
   ```bash
   pipenv install            # picks up quart from the Pipfile
   cd frontend && npm install && npm run build   # outputs frontend/dist
   ```
2. **Apply pending DB migrations** via the ledgered runner (see "Database setup"
   in the main [`README.md`](../README.md#database-setup)):
   ```bash
   python migrate.py            # apply every pending migration in order
   python migrate.py status     # show which migrations are applied vs pending
   ```
   Do **not** apply `migrations/*.sql` files directly with `mysql` — the runner
   tracks what has run in a `schema_migrations` table, and a direct apply skips
   that ledger and desyncs later `migrate.py` runs.
3. **Create a Discord OAuth app** (Developer Portal → your application → OAuth2).
   Add the redirect `https://<site-hostname>/api/auth/callback` and copy the
   client id/secret. The redirect URI must be HTTPS (Discord rejects non-HTTPS
   except `http://localhost`), but it may be an internal hostname with an
   internal cert — Discord only redirects the browser, it never connects to or
   validates your server.
4. **Configure** the `web` block in `config.json` (see `config.json.dist`):
   `enabled: true`, a long random `session_secret`, the OAuth client id/secret,
   `oauth_redirect_uri`, `frontend_url`, and **`host: "0.0.0.0"`** if the
   reverse proxy runs on a different box (`127.0.0.1` only allows same-box).
5. **Reverse proxy** (nginx). Quart serves the built SPA itself, so the whole
   dashboard is a single upstream — just proxy the hostname to the bot box's
   `:8099`, including the WebSocket upgrade. See the reference below.
6. **Restart the bot.** With `web.enabled` true it starts the dashboard; the
   activity listener begins recording immediately.

> Existing hunts have `role_id = 0` until backfilled, so they are visible to all
> members. New hunts created with a team role capture it automatically. To
> backfill, set `hunts.role_id` to the team role's Discord id.

## Serving model

Quart serves both the API (`/api/*`, incl. the `/api/ws/activity` WebSocket) and
the built SPA (`frontend/dist`, with SPA-route fallback). Everything lives under
one origin, so there is **one upstream** and **no CORS**. Point the cert,
`server_name`, the Discord redirect URI, and the config all at the *same*
hostname.

## Reference deployment (test site: `puzzweb.home.meldon.net`)

Topology: nginx on the edge box (`souwester`) terminates TLS and reverse-proxies
to the bot box (`redhat.home.meldon.net`), which runs the bot + embedded Quart on
`:8099`. The hostname is internal-only, served with an internal cert — fine,
because Discord never validates it (team browsers must trust the internal CA or
click through once).

**`config.json` `web` block:**
```jsonc
"web": {
  "enabled": true,
  "host": "0.0.0.0",
  "port": 8099,
  "session_secret": "<long random string>",
  "oauth_client_id": "<from Discord>",
  "oauth_client_secret": "<from Discord>",
  "oauth_redirect_uri": "https://puzzweb.home.meldon.net/api/auth/callback",
  "frontend_url": "https://puzzweb.home.meldon.net"
}
```
Register `https://puzzweb.home.meldon.net/api/auth/callback` as the Discord
redirect, and ensure the internal cert's SAN includes `puzzweb.home.meldon.net`.

**nginx on souwester.** This mirrors the canonical proxy block in
[README.md § 7](README.md#7-dashboard-reverse-proxy-with-nginx--tls) with this
site's hostnames filled in — if you change the proxy directives (headers,
timeouts, WebSocket upgrade), update both copies (and the Plesk variant in
README.md § 7) together. Define the WebSocket upgrade map once, globally, in
`http{}` context (so it is reusable and won't clash when you add more sites):
```nginx
# /etc/nginx/conf.d/websocket_upgrade.conf
map $http_upgrade $connection_upgrade { default upgrade; '' close; }
```
Then the site (`sites-available/puzzweb` → symlink into `sites-enabled`, then
`sudo nginx -t && sudo systemctl reload nginx`):
```nginx
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;
    server_name puzzweb.home.meldon.net;

    include /etc/nginx/snippets/home-meldon-ssl.conf;   # cert for this name

    location / {
        proxy_pass http://redhat.home.meldon.net:8099;
        proxy_http_version 1.1;

        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_set_header Upgrade    $http_upgrade;
        proxy_set_header Connection $connection_upgrade;

        # The activity WebSocket is idle between messages; the 60s default would
        # cut it every minute (the SPA reconnects, but this avoids the churn).
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
```

Also required on the bot box:
- **Firewall:** allow `souwester → tcp/8099` on the LAN. Keep `8099` off any
  external port-forward; only souwester needs it.
- **DNS:** souwester must resolve `redhat.home.meldon.net` (the proxy target)
  and clients must resolve `puzzweb.home.meldon.net` (the browser-facing name).

Optional: add a `listen 80` server block for `puzzweb.home.meldon.net` that
301-redirects to HTTPS, so plain `http://` requests land on the secure site.

## Local development

Run the bot (with `web.enabled` and an OAuth app pointing at
`http://localhost:5173/api/auth/callback`), then:
```bash
cd frontend && npm run dev   # Vite on :5173, proxies /api to :8099
```
