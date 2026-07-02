# QDPuzzleBot — Discord Puzzle Hunt Manager

The Discord puzzle-hunt manager used by the **Quarantine Decrypters** team. It creates and
organises puzzle channels, tracks puzzle/round/meta status, and (optionally) wires each puzzle
up to a Google Sheet and a web dashboard.

It is a fork of a [bot originally by Ladder Dogs](https://github.com/azjps/ladder_dogs_discord_bot),
which itself started from [`cookiecutter-discord.py-postgres`](https://github.com/makupi/cookiecutter-discord.py-postgres).
It is built on [discord.py](https://discordpy.readthedocs.io/) and uses
[`google-api-python-client`](https://github.com/googleapis/google-api-python-client) for the
(optional) Google Sheets integration.

**Storage:** puzzle/round/hunt metadata lives in a **MySQL/MariaDB** database (configured via
`storage: "mysql"` and the `mysql_username` / `mysql_password` / `database` keys in `config.json`).
The original JSON-file / Postgres storage described in earlier versions of this README is no longer
used. Schema is managed by ordered migrations under [`migrations/`](migrations/) (see
[Database setup](#database-setup)).

**Dashboard (optional):** a React + [Mantine](https://mantine.dev/) single-page app (`frontend/`)
talks to an embedded [Quart](https://quart.palletsprojects.com/) API (`bot/web/`) to provide a
read/write web view of the board with Discord-OAuth login. It is off by default; enable it under
the `web` key in `config.json`. See [Deploying on a Linux server](#deploying-on-a-linux-server)
for a full production walkthrough.

# Usage

## Creating a hunt

For example, run the command

`!hunt MysteryHunt2021:https://perpendicular.institute/`

This hunt URL will be used to guess the link to the puzzle when new puzzles are posted. If the generated
puzzle link is wrong, it can be updated by posting `!link https://correct-hunt-website-link` in the puzzle channel.

Users with the `manage_channel` role can update administrative settings via `!update_setting {key} {value}`, where
all of the settings can be viewed via `!show_settings`.

There are also various settings and links to Google Drive that should be updated prior to the start of hunt,
like

- `drive_parent_id`: the Google drive ID of the parent folder the hunt will be created under
- `drive_resources_id`: optional, Google drive ID of a resources Google doc
- `discord_bot_channel`: if set, most bot commands must be entered in that channel name
- `discord_use_voice_channels`: false by default; if true, voice channels are created alongside text channels

## During hunt

Most users will just need to become familiar with two commands:
1. To post a new puzzle channel, post `!p puzzle-name` in the `#meta` channel of the corresponding puzzle round.
   This will create a new text channel where puzzle discussion can take place, as well as
   a new Google Spreadsheet with a handy `Quick Links` worksheet.
   You can scroll the sidebar or use `Ctrl + K` to help search for existing puzzle channels.
2. When Hunt HQ has confirmed that the puzzle has been solved, post `!solve SOLUTION` in the puzzle channel.
   The channels will be automatically archived afterwards.

----

For a new round/world of puzzles, first start by posting `!round` in the `#bot` channel:
```
!r puzzle-round-name
```
This will create a `#puzzleround-name` [category](https://support.discord.com/hc/en-us/articles/115001580171-Channel-Categories-101)
along with a `#meta` puzzle text for the round. The `#meta` channels are the place for general discussion about the round,
as well as discussion about the meta puzzle (if there is more than one meta, creating new puzzle channels would be prudent).

For a new puzzle, one can either post the puzzle via `!puzzle` in the `#bot` channel:
```
!p puzzle-round-name: puzzle-name
```
Or simply `!p puzzle-name` in the corresponding round's `#meta` channel. This will create a `#puzzle-name` text and voice channel
where discussion of the puzzle can take place.

When the puzzle is solved, post `!solve SOLUTION` in the puzzle's channel. The text channel will automatically get archived (moved
to the `#solved-puzzles` category) after ~5 minutes, and the voice channel will be deleted. If this is mistakenly entered,
this can be undone by posting `!unsolve`.

There are various other commands for updating the status, type, priority, and notes associated with a puzzle.
These fields are mainly for others to easily find out about the status of other puzzles. They can be retrieved
on the discord channels using the corresponding commands (see `!info` for the available commands), or viewed
in aggregate on the Nexus spreadsheet, where all puzzles and links are listed.

## Google Drive

When a puzzle channel is created, if Google Drive integration is enabled, a corresponding spreadsheet is created
in the folder for the puzzle round in the root Google Drive directory. The spreadsheet will have a secondary
"Quick Links" tab created for convenience.

![Puzzle spreadsheet Quick Links tab example](docs/gsheet_puzzle_quick_links.png)

The bot periodically updates a "nexus spreadsheet" which shows a list of all puzzles along with relevant information
such as the puzzle url, spreadsheet link. (The formatting of the nexus spreadsheet can be done manually by the user;
the bot only populates the contents of the spreadsheet cells.)

![Nexus spreadsheet example](docs/gsheet_nexus_example.png)

# Setup

Clone this repository
```
git clone https://github.com/sloop-puzzles/puzzlehunt-discordbot
```
Create a [discord application, bot](https://realpython.com/how-to-make-a-discord-bot-python/), and add the bot's token to a [`config.json` file](https://github.com/makupi/cookiecutter-discord.py-postgres/blob/master/%7B%7Bcookiecutter.bot_slug%7D%7D/config.json) in the root directory of this project:
```json
{
  "discord_bot_token": "{{discord_bot_token}}",
  "prefix": "!",
  "storage": "mysql",
  "mysql_username": "puzzlebot",
  "mysql_password": "{{mysql_password}}",
  "database": "puzzlebot"
}
```
The bot connects to a local MySQL server using `mysql_username` / `mysql_password` and the
`database` (schema) name. To also enable the optional web dashboard, add a `web` block:
```json
{
  "web": {
    "enabled": true,
    "host": "127.0.0.1",
    "port": 8099,
    "session_secret": "{{a long random string}}",
    "oauth_client_id": "{{discord_oauth_client_id}}",
    "oauth_client_secret": "{{discord_oauth_client_secret}}",
    "oauth_redirect_uri": "https://your-host/api/auth/callback",
    "frontend_url": "https://your-host/"
  }
}
```

For the Google Drive integration (optional), create a [Google service account (for example see these instructions from `gspread`)](
https://gspread.readthedocs.io/en/latest/oauth2.html#enable-api-access), and save the service account key JSON file as `google_secrets.json`.

## Database setup

The bot needs a MySQL/MariaDB database. The full schema is checked in under
[`migrations/`](migrations/) as ordered `NNN_*.sql` files, applied by the
migration runner in [`migrate.py`](migrate.py). The runner reads the same
`config.json` (`mysql_username` / `mysql_password` / `database`) the bot uses
and tracks which files have run in a `schema_migrations` table, so each applies
exactly once.

1. Create the database and a user (once), e.g. in the `mysql` client:
   ```sql
   CREATE DATABASE puzzlebot CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
   CREATE USER 'puzzlebot'@'localhost' IDENTIFIED BY '...';
   GRANT ALL PRIVILEGES ON puzzlebot.* TO 'puzzlebot'@'localhost';
   ```
   Put the matching `mysql_username` / `mysql_password` / `database` in `config.json`.

2. Apply the migrations (run inside the pipenv shell / virtualenv):
   ```bash
   python migrate.py            # apply every pending migration in order
   python migrate.py status     # show which migrations are applied vs pending
   ```

This stands a fresh database up from scratch (`000_base_schema.sql` onward). To
add a schema change later, drop a new `NNN_description.sql` file in
`migrations/` (use full-line `--` comments; separate statements with `;`) and
re-run `python migrate.py`.

**Golden rule: migration → deploy → code.** The bot's generic `commit()` writes
every dataclass field by name, so a column must exist in the database *before*
code that references it ships. Always run pending migrations first.

**Already have a hand-built database?** If your schema predates this runner, tell
it the existing migrations are already in place so it won't try to re-create
tables (which would error):
```bash
python migrate.py baseline   # record all present migrations as applied, run nothing
```
After baselining, future `python migrate.py` runs apply only genuinely new files.

Now you can run the bot by running the following in a shell:
```bash
# Setup python3 environment
pip install pipenv
pipenv install  # creates a new virtualenv
pipenv shell
# Start bot
python run.py
```
The environment variable `$LADDER_SPOT_DATA_DIR` can be used to control the directory where guild settings and puzzle data are stored.

# Deploying on a Linux server

This section is a complete walkthrough for standing the bot up on a Linux host, including the
optional web dashboard and **getting Discord OAuth working**. For the dashboard the authoritative,
deeper reference (serving model, nginx WebSocket details, the live test-site topology) is
[`docs/dashboard.md`](docs/dashboard.md); this README gives the end-to-end happy path.

## 1. Host prerequisites

Install the system packages. **Debian/Ubuntu:**

```bash
sudo apt update
sudo apt install -y git python3 python3-pip mariadb-server nginx
pip3 install --user pipenv          # or: sudo apt install pipenv
sudo apt install -y nodejs npm      # dashboard only: Node.js 18+ to build the SPA
```

**AlmaLinux / RHEL-family** (`dnf`):

```bash
sudo dnf install -y git python3 python3-pip mariadb-server nginx
pip3 install --user pipenv
sudo dnf module install -y nodejs:20   # dashboard only: Node.js 18+ to build the SPA
sudo systemctl enable --now mariadb
```

> **On a Plesk-managed AlmaLinux host**, MariaDB and nginx are **already installed and managed by
> Plesk** — don't install or hand-configure them. You only need **Python 3 + pipenv** and (for the
> dashboard) **Node.js**. Create the bot's database through the Plesk **Databases** UI (step 3) and
> set up the reverse proxy + TLS through Plesk (step 7), not by editing `/etc/nginx` directly.
> Node.js can come from `dnf` as above or from Plesk's **Node.js** extension.

For the optional dashboard you need **Node.js 18+** to build the SPA (installed above); if your
distro ships an older Node, use [nodesource](https://github.com/nodesource/distributions).

Clone the repo to wherever the service will live. On Plesk, a tidy choice is under the subscription
that will host the dashboard subdomain (e.g. `/var/www/vhosts/<domain>/puzzbot`), or any dedicated
service user's home:

```bash
git clone https://github.com/sloop-puzzles/puzzlehunt-discordbot puzzbot
cd puzzbot
```

## 2. Create the Discord application & bot

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) → **New
   Application** (or open an existing one).
2. **Bot** tab → *Add Bot*. Copy the **token** → this becomes `discord_bot_token` in `config.json`.
3. **Privileged Gateway Intents** — the bot runs with `discord.Intents.all()`, so enable **all
   three**: *Presence*, *Server Members*, and *Message Content*. (Server Members is required for
   the dashboard's role-based hunt visibility; Message Content for the `!` bang commands.)
4. **OAuth2 → URL Generator** to invite the bot: select the `bot` scope and the permissions it
   needs (Manage Channels, Manage Roles, Send Messages, Read Message History, Manage Messages,
   and — if you use voice channels — Connect/Move Members). Open the generated URL and add the bot
   to your server.

## 3. Database

Follow [Database setup](#database-setup) above: create the database + user, put the credentials in
`config.json`, and run `python migrate.py` to apply every migration. Do this **before** first
starting the bot.

> **On Plesk:** create the database and user in **Websites & Domains → Databases → Add Database**
> (pick MySQL/MariaDB, set a user + password) instead of the `CREATE DATABASE`/`CREATE USER` SQL.
> Put the Plesk-generated database name, user and password into `config.json`. Plesk's DB user
> connects over `localhost`, which is what the bot uses. Then still run `python migrate.py` from the
> repo to apply the schema migrations.

## 4. (Dashboard) set up Discord OAuth

The dashboard logs users in with Discord OAuth using only the `identify` scope — it tells the site
*who* the user is, then the running bot looks up that user's guild membership and team roles from
its own cache (so the bot must be in the guild with the Server Members intent on). Skip this whole
step if you are not enabling the dashboard.

1. In the Developer Portal → your application → **OAuth2** → **Redirects**, add the callback URL:
   ```
   https://<your-dashboard-hostname>/api/auth/callback
   ```
   The redirect **must be HTTPS** (Discord rejects non-HTTPS, except `http://localhost` for local
   dev). It may be an internal hostname with an internal/self-signed cert — Discord only redirects
   the user's browser, it never connects to your server, so it never validates the cert.
2. Copy the **Client ID** and **Client Secret** from the OAuth2 page → these become
   `oauth_client_id` / `oauth_client_secret` in the `web` block.
3. Point `oauth_redirect_uri` (config) at the *exact* same URL you registered, and `frontend_url`
   at the dashboard's base URL. The cert `server_name`, the registered redirect, and these config
   values must all use the **same hostname**.

## 5. Configure `config.json`

Copy the template and fill it in (see [`config.json.dist`](config.json.dist)):

```bash
cp config.json.dist config.json
```

A full dashboard-enabled config looks like:

```jsonc
{
  "discord_bot_token": "<bot token from step 2>",
  "prefix": "!",
  "storage": "mysql",
  "database": "puzzlebot",
  "mysql_username": "puzzlebot",
  "mysql_password": "<db password>",
  "web": {
    "enabled": true,
    "host": "0.0.0.0",          // 0.0.0.0 if the reverse proxy is on another box; 127.0.0.1 if same-box
    "port": 8099,
    "session_secret": "<a long random string>",
    "oauth_client_id": "<from step 4>",
    "oauth_client_secret": "<from step 4>",
    "oauth_redirect_uri": "https://your-host/api/auth/callback",
    "frontend_url": "https://your-host"
  }
}
```

If you use Google Sheets, also drop the service-account key in `google_secrets.json` (see
[Setup](#setup)).

## 6. Build the dashboard & start the bot

`./start.sh` builds the SPA (`frontend/dist`) and then starts the bot, which serves both the API
and the built SPA from one process on `web.port`. A failed frontend build is non-fatal — the bot
still starts and serves the previous build:

```bash
pipenv install                  # production deps
./start.sh                      # foreground; builds frontend then runs the bot
# Background:   nohup ./start.sh > bot.log 2>&1 &
# Restart:      ./start.sh --restart
```

### Running it as a systemd service

For an unattended deployment, prefer a unit over `nohup`. Create
`/etc/systemd/system/puzzbot.service` (adjust `User` and `WorkingDirectory`):

```ini
[Unit]
Description=QDPuzzleBot Discord puzzle-hunt manager
After=network-online.target mariadb.service
Wants=network-online.target

[Service]
Type=simple
User=puzzbot
WorkingDirectory=/home/puzzbot/puzzbot
ExecStart=/home/puzzbot/puzzbot/start.sh
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now puzzbot
sudo journalctl -u puzzbot -f      # follow logs
```

### Redeploying with `deploy.sh` (Plesk Git)

[`deploy.sh`](deploy.sh) runs the post-pull steps in the right order: it syncs Python deps
(`pipenv install --deploy`), applies pending DB migrations (`migrate.py`), then restarts the bot.
Migrations run **before** the restart, so a failed migration aborts the deploy and leaves the
running bot untouched (the migrate→deploy→code golden rule).

On a Plesk-managed host, wire it into **Websites & Domains → (your domain) → Git → Additional
deployment actions**. Plesk pulls the repo and then runs that box as your **subscription system
user** (Hosting Settings → *System user*) — an *unprivileged* account, non-interactively. Two
consequences:

- **The restart can't use plain `sudo systemctl restart` unattended** — it would prompt for a
  password and fail. `deploy.sh` reads a `RESTART_CMD` env var so you can pick the restart that
  suits how the bot runs. Set it inline in the deployment-actions box (Plesk has no separate
  env-var panel):

  ```bash
  RESTART_CMD='sudo -n systemctl restart puzzbot' ./deploy.sh
  ```

  For that `sudo -n` to work without a password, add a one-time rule as root (replace
  `<deploy-user>` with the subscription system user; confirm the `systemctl` path with
  `command -v systemctl`):

  ```
  # /etc/sudoers.d/puzzbot
  <deploy-user> ALL=(root) NOPASSWD: /usr/bin/systemctl restart puzzbot
  ```

  Alternatives for `RESTART_CMD`: `systemctl --user restart puzzbot` (user-level unit +
  `loginctl enable-linger <deploy-user>`, no sudo at all). If you leave `RESTART_CMD` **unset**,
  `deploy.sh` falls back to relaunching the bot as a detached user process via
  `start.sh --restart` — zero privileges, but no systemd supervision or reboot-persistence, so
  only use that if you are *not* running the systemd unit above (running both would start two bots
  fighting over the token and `:8099`).

- **Don't restart the bot with a foreground command in the hook** — the deploy action must return.
  `deploy.sh` already detaches its fallback restart, and `systemctl restart` returns immediately,
  so either path is safe.

## 7. (Dashboard) reverse proxy with nginx + TLS

Quart serves the API and the SPA under one origin, so there is **one upstream and no CORS** — just
proxy your HTTPS hostname to the bot's `:8099`, including the WebSocket upgrade for the live
activity feed. Terminate TLS at nginx (Let's Encrypt for a public name, or an internal CA for an
internal one).

Define the WebSocket upgrade map once, globally:

```nginx
# /etc/nginx/conf.d/websocket_upgrade.conf
map $http_upgrade $connection_upgrade { default upgrade; '' close; }
```

Then the site (e.g. `/etc/nginx/sites-available/puzzbot`, symlinked into `sites-enabled`):

```nginx
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;
    server_name your-host;

    ssl_certificate     /etc/letsencrypt/live/your-host/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-host/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8099;   # or the bot box's LAN address:8099
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

# Optional: redirect plain HTTP to HTTPS
server {
    listen 80;
    server_name your-host;
    return 301 https://$host$request_uri;
}
```

Validate and reload:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

If the bot and nginx are on **different boxes**, set `web.host` to `0.0.0.0`, point `proxy_pass`
at the bot box's LAN address, and allow `nginx-box → tcp/8099` on the LAN firewall (keep `8099`
off any external port-forward).

**AlmaLinux / RHEL-family — SELinux.** With SELinux enforcing, nginx is **blocked from making the
outbound proxy connection** by default and you'll get `502` + an `AVC denied` audit log. Allow it:

```bash
sudo setsebool -P httpd_can_network_connect 1
```

### Plesk-managed hosts

Don't hand-edit vhosts on a Plesk box — Plesk owns the nginx config and will overwrite them. Instead:

1. **Create a subdomain** for the dashboard in **Websites & Domains** (e.g.
   `puzzweb.your-domain.com`). Point `oauth_redirect_uri` / `frontend_url` / the Discord redirect
   at this hostname.
2. **Issue TLS** with the **SSL It!** extension (Let's Encrypt) for that subdomain — Plesk handles
   renewal.
3. **Turn the docroot into a pure proxy.** Under the subdomain → **Apache & nginx Settings**:
   - tick **Proxy mode** off-loaded to nginx as needed, and **untick "Smart static files
     processing"** so nginx doesn't try to serve files from the docroot (Quart serves the SPA).
   - In **Additional nginx directives**, add the proxy (Plesk drops these into the `server{}` block,
     so reference a map defined globally — see note):
     ```nginx
     location / {
         proxy_pass http://127.0.0.1:8099;
         proxy_http_version 1.1;

         proxy_set_header Host              $host;
         proxy_set_header X-Real-IP         $remote_addr;
         proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
         proxy_set_header X-Forwarded-Proto $scheme;

         proxy_set_header Upgrade    $http_upgrade;
         proxy_set_header Connection $puzzbot_conn_upgrade;

         proxy_read_timeout 3600s;
         proxy_send_timeout 3600s;
     }
     ```
4. **Define the WebSocket upgrade map once, globally** (the `map` directive is only valid in
   `http{}` context, which Plesk's per-domain box is not). Plesk includes `/etc/nginx/conf.d/*.conf`
   in its `http{}`, so add a file there with a uniquely-named variable to avoid clashing with any
   Plesk-provided map:
   ```nginx
   # /etc/nginx/conf.d/puzzbot_ws.conf
   map $http_upgrade $puzzbot_conn_upgrade { default upgrade; '' close; }
   ```
   Then `sudo nginx -t && sudo systemctl reload nginx`. (Plesk also regenerates config on
   subscription changes; this `conf.d` file is global and survives that.)
5. **SELinux** still applies — run the `httpd_can_network_connect` boolean above so Plesk's nginx
   can reach `127.0.0.1:8099`.

Keep the bot's `web.host` at `127.0.0.1` (same-box) so `:8099` is never exposed directly — only
Plesk's nginx reaches it.

## 8. Verify

- Bot logs in to Discord and responds to `!help` in the server.
- Browse to `https://your-host/`, click login → you are bounced to Discord, authorise, and land
  back on the board.
- The puzzle list and live activity feed populate (an active hunt is visible to holders of its
  team role; see [`docs/dashboard.md`](docs/dashboard.md) for the visibility model).

## Tests

Use `pipenv install --dev` to install dev packages, and in the repo root directory, run
```bash
timeout 120 pipenv run python -m pytest --compact
```
Add `--parallel` for multi-file or suite runs.

# Credits

Inspired by various open source discord bot python projects like [cookiecutter-discord.py-postgres](https://github.com/makupi/cookiecutter-discord.py-postgres) and [discord-pretty-help](https://github.com/stroupbslayen/discord-pretty-help/). Licensed under [GPL 3.0](https://choosealicense.com/licenses/gpl-3.0/) (due to the aforementioned `cookiecutter`).
