# Puzzle Dashboard (Phase 1)

A React/Mantine SPA over a Quart JSON API embedded in the bot process.

Phase 1 (read): hunts grouped by meta, solutions, a filterable puzzle list, and
live Discord activity. Phase 2 (write, organiser-gated except notes): add/erase
notes, retag puzzles between metas (drag board + add/remove menu), and create
puzzles and metas. Write-actions run through ctx-free `web_*` methods on the
Puzzles cog, so the website creates real Discord channels + Google sheets exactly
like the matching `!` commands.

## Architecture

- `bot/web/` — Quart app started from `setup_hook` (only when `web.enabled`).
  - `auth.py` — Discord OAuth (`identify` scope) → httpOnly session cookie.
    Membership and team roles come from the live bot, not OAuth scopes.
  - `api.py` — `/api/hunts`, `/api/hunts/<uid>`, `/api/hunts/<uid>/puzzles`,
    and the `/api/ws/activity` WebSocket.
  - `data.py` — dashboard read queries on a dedicated connection pool, run off
    the event loop via `asyncio.to_thread` (never shares the bot's connection).
  - `events.py` — in-process pub/sub feeding the activity WebSocket.
- `bot/cogs/activity.py` — records the last human message per channel into
  `channel_activity` and publishes a live event.
- `frontend/` — Vite + React + TS + Mantine SPA.

## Hunt visibility

- **Active** hunts are visible only to holders of the hunt's team `role_id`
  (legacy hunts with `role_id = 0` fall back to any guild member).
- **Archived** hunts appear in the "Old hunts" menu, visible to any guild member.

See `memory/site-hunt-visibility.md` for the full model.

## Deploy steps (run when ready)

1. **Install deps** (additive; safe while the bot runs):
   ```bash
   pipenv install            # picks up quart from the Pipfile
   cd frontend && npm install && npm run build   # outputs frontend/dist
   ```
2. **Run the DB migrations** (applies every pending migration under
   `migrations/`, tracked in `schema_migrations` — see [README.md § Database
   setup](README.md#database-setup)):
   ```bash
   python migrate.py
   ```
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
