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

An optional **message bridge** additionally mirrors each puzzle channel's Discord chat into the
dashboard's in-app chat viewer, so an archived hunt keeps a self-contained transcript after its
Discord channels are gone. It too is off by default — see
[`docs/dashboard.md`](docs/dashboard.md#message-bridge-chat-viewer) to enable it.

An optional **Chrome extension** (`extension/`) shows a puzzle's Discord chat in a side panel
beside its Google Sheet and adds dashboard shortcuts for opening puzzles. See
[`extension/README.md`](extension/README.md) and the `web.extension_origins` key in
[`docs/dashboard.md`](docs/dashboard.md#chrome-extension-origins).

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

For a new puzzle, post `!p puzzle-name` in the corresponding round's `#meta` channel to create it in that round, or in the
`#bot` channel to create it unassigned to any round:
```
!p puzzle-name
```
This will create a `#puzzle-name` text and voice channel where discussion of the puzzle can take place.

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
git clone https://github.com/uhtoff/QDPuzzleBot
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

# Deploying on a Linux server

This section is a complete walkthrough for standing the bot up on a Linux host, including the
optional web dashboard and **getting Discord OAuth working**. For the dashboard the authoritative,
deeper reference (serving model, nginx WebSocket details, the live test-site topology) is
[`docs/dashboard.md`](docs/dashboard.md); this README gives the end-to-end happy path.

It assumes a **Plesk-managed AlmaLinux host** running **two instances of the bot side by side** —
a **live** bot and a **test** bot — isolated by Linux user. Running them as different system
users keeps their processes, virtualenvs, configs, logs and per-user systemd units fully separate:
`start.sh` already scopes its "already running?" check per user (`pgrep -u`), so the two never
fight over each other's process. The four things they must **not** share are the **web port**, the
**database**, the **subdomain**, and the **Discord application**.

Pick concrete values up front and keep them straight — everything below is the same steps run
twice, once per column:

| | **Live** | **Test** |
|---|---|---|
| System user | `puzzbot` | `puzzbot-test` |
| Repo clone | `/home/puzzbot/puzzbot` | `/home/puzzbot-test/puzzbot` |
| Git branch | `main` | `staging` |
| Database / DB user | `puzzlebot` | `puzzlebot_test` |
| `web.port` | `8099` | `8098` |
| Dashboard subdomain | `puzzweb.<domain>` | `puzzweb-test.<domain>` |
| Discord application | live bot app | separate test bot app |
| systemd unit | `--user puzzbot` (as `puzzbot`) | `--user puzzbot` (as `puzzbot-test`) |

Use **two separate Discord applications** (own token + OAuth client id/secret each) so a test
action never touches the live server or spends the live token, and each gets its own OAuth
redirect. The two bots can share one Discord guild or live in separate test/live guilds.

> **One box, two users, per-user systemd.** Each user runs its own bot under a **user-level**
> systemd unit (`systemctl --user`), so neither needs root and both survive logout and reboot via
> `loginctl enable-linger`. Plesk only terminates TLS and reverse-proxies each subdomain to that
> instance's port; it does not run the bot. If you only want a **single** instance, just ignore the
> "Test" column throughout.

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

### Python 3.12 via pyenv

The bot needs **Python 3.12**. If the host's system `python3` is older (common on
RHEL/AlmaLinux), install 3.12 per service user with [pyenv](https://github.com/pyenv/pyenv)
rather than replacing the system Python. Install the build deps once as root, then run the rest
**as each service user** (`puzzbot` / `puzzbot-test`) — pyenv installs into that user's `$HOME`:

```bash
# build deps — as root, one time for the box (RHEL/AlmaLinux)
sudo dnf install -y gcc make patch zlib-devel bzip2 bzip2-devel readline-devel \
  sqlite sqlite-devel openssl-devel tk-devel libffi-devel xz-devel

# as the service user
curl -fsSL https://pyenv.run | bash

# add pyenv to the shell — also append these three lines to ~/.bashrc so they persist
export PYENV_ROOT="$HOME/.pyenv"
export PATH="$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init - bash)"

pyenv install 3.12
pyenv global 3.12        # or `pyenv local 3.12` inside the repo dir
```

The `Pipfile` pins `python_version = "3.12"`, so `pipenv install` fails immediately if the
active interpreter isn't 3.12 — a clear error instead of a confusing downstream failure.

> **Use a recent pipenv.** Python 3.12 removed `distutils` from the stdlib, and older pipenv
> probes install paths by importing it — so a stale distro pipenv fails on 3.12 with
> `ModuleNotFoundError: No module named 'distutils'`, which cascades into *every* package
> "failing to install". Upgrade first (want a 2023.11+/2024+ version):
>
> ```bash
> pip3 install --user --upgrade pip pipenv
> pipenv --rm                     # discard any half-built venv
> pipenv --python 3.12 install
> ```

> **`pipenv: command not found`?** `pip3 install --user pipenv` puts the binary in
> `~/.local/bin`, which isn't on `PATH` by default. Add it (append to `~/.bashrc` too), then
> pin the interpreter when creating the virtualenv:
>
> ```bash
> export PATH="$HOME/.local/bin:$PATH"
> pipenv --python 3.12 install
> ```
>
> If `su`-ing in gives no `pipenv`/`pyenv` on PATH, you're in a non-login shell — the service
> user's shell is `/bin/false`, so use a login shell: `su -s /bin/bash - <user>` (note the `-`).

For the optional dashboard you need **Node.js 18+** to build the SPA (installed above); if your
distro ships an older Node, use [nodesource](https://github.com/nodesource/distributions).

Create the two service users (as root), enable linger so their user-level systemd managers start
at boot and survive logout, then clone the repo into each user's home on the branch that instance
tracks. Do the clone **as that user**:

```bash
# as root — create the accounts
useradd -m puzzbot
useradd -m puzzbot-test
loginctl enable-linger puzzbot puzzbot-test

# as puzzbot (live → main)
git clone https://github.com/uhtoff/QDPuzzleBot puzzbot
git -C puzzbot checkout main

# as puzzbot-test (test → staging)
git clone https://github.com/uhtoff/QDPuzzleBot puzzbot
git -C puzzbot checkout staging
```

Each instance now lives at `/home/<user>/puzzbot`. On a Plesk box you can instead point each
instance's clone at the subscription that will host its subdomain and make that subscription's
system user the instance user — that lets Plesk's per-domain **Git** auto-deploy run `deploy.sh`
as the right user (see [Redeploying with `deploy.sh`](#redeploying-with-deploysh-plesk-git)).

> Standing up a **Discord server** for a hunt (as opposed to the bot instance below) is a
> separate checklist — see [docs/new-server-playbook.md](docs/new-server-playbook.md). It
> includes two manual steps nothing in the code performs: pinning the admin/organiser roles
> with `!set_admin_roles`, and setting the team role's permission overwrite on the hunt's
> initial category.

## 2. Create the Discord application & bot

Do this **twice** — one application for the live bot and a separate one for the test bot — so the
two never share a token. Keep the two tokens straight; each goes in that instance's `config.json`.

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

Create **one database per instance** — `puzzlebot` for live and `puzzlebot_test` for test — each
with its own user, so the test bot can never write to live data. Run `python migrate.py` inside
each instance's checkout (it reads that instance's `config.json`).

> **On Plesk:** create each database and user in **Websites & Domains → Databases → Add Database**
> (pick MySQL/MariaDB, set a user + password) instead of the `CREATE DATABASE`/`CREATE USER` SQL.
> Add two — `puzzlebot` and `puzzlebot_test`. Put each Plesk-generated database name, user and
> password into the matching instance's `config.json`. Plesk's DB user connects over `localhost`,
> which is what the bot uses. Then still run `python migrate.py` from each repo to apply the schema
> migrations.

## 4. (Dashboard) set up Discord OAuth

The dashboard logs users in with Discord OAuth using only the `identify` scope — it tells the site
*who* the user is, then the running bot looks up that user's guild membership and team roles from
its own cache (so the bot must be in the guild with the Server Members intent on). Skip this whole
step if you are not enabling the dashboard.

Do this in **each** of the two Discord applications from step 2, using that instance's subdomain —
`puzzweb.<domain>` for live, `puzzweb-test.<domain>` for test.

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

Copy the template and fill it in (see [`config.json.dist`](config.json.dist)) **in each instance's
checkout** — `config.json` is per-user and gitignored, so the two never collide:

```bash
cp config.json.dist config.json
```

A full dashboard-enabled **live** config looks like:

```jsonc
{
  "discord_bot_token": "<LIVE bot token from step 2>",
  "prefix": "!",
  "storage": "mysql",
  "database": "puzzlebot",
  "mysql_username": "puzzlebot",
  "mysql_password": "<db password>",
  "web": {
    "enabled": true,
    "host": "127.0.0.1",        // 127.0.0.1: Plesk's nginx is same-box. Use 0.0.0.0 only if the proxy is another box.
    "port": 8099,
    "session_secret": "<a long random string>",
    "oauth_client_id": "<live app, from step 4>",
    "oauth_client_secret": "<live app, from step 4>",
    "oauth_redirect_uri": "https://puzzweb.<domain>/api/auth/callback",
    "frontend_url": "https://puzzweb.<domain>"
  }
}
```

The **test** config is the same file with every shared value swapped to its own column: the
**test** bot token, `"database": "puzzlebot_test"` (+ its user/password), `"port": 8098`, the
**test** app's OAuth id/secret, and the `puzzweb-test.<domain>` redirect/frontend URLs. The
distinct `port` is what lets both bots bind on one box — never give them the same one.

If you use Google Sheets, also drop the service-account key in `google_secrets.json` (see
[Setup](#setup)).

## 6. Build the dashboard & start the bot

`./start.sh` builds the SPA (`frontend/dist`) and then starts the bot, which serves both the API
and the built SPA from one process on `web.port`. A failed frontend build is non-fatal — the bot
still starts and serves the previous build. Run this **as each instance's user**, in that user's
checkout:

```bash
pipenv install                  # production deps
./start.sh                      # foreground; builds frontend then runs the bot
# Background:   nohup ./start.sh > bot.log 2>&1 &
# Restart:      ./start.sh --restart
```

### Running each instance as a user systemd service

For an unattended deployment, prefer a unit over `nohup`. With two bots on one box under two users,
run each under a **user-level** systemd unit — no root, cleanly isolated per account, and it
survives logout and reboot once linger is on (enabled back in step 1). As **each** user (`puzzbot`,
then `puzzbot-test`), create `~/.config/systemd/user/puzzbot.service`:

```ini
[Unit]
Description=QDPuzzleBot Discord puzzle-hunt manager
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=%h/puzzbot
ExecStart=%h/puzzbot/start.sh
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

`%h` expands to the user's home, so the **same** file works verbatim for both accounts — they live
in separate per-user managers, so there is no name clash. Then, as each user:

```bash
mkdir -p ~/.config/systemd/user      # if it doesn't exist yet
systemctl --user daemon-reload
systemctl --user enable --now puzzbot
systemctl --user status puzzbot
journalctl --user -u puzzbot -f      # follow logs
```

> **`systemctl --user` → "Failed to connect to bus: No such file or directory"?** The unit can be
> running perfectly (under linger) while an *interactive* shell still can't reach it. This happens
> when you enter the account with `su` / `su -` (or a Plesk shell) instead of a real login: `su`
> doesn't start a user session, so `XDG_RUNTIME_DIR` and the user D-Bus aren't set. Fixes, best
> first:
> - **SSH in directly as the user** (on Plesk: Hosting Settings → *Access to the server over SSH* →
>   `/bin/bash`) — a real login sets `XDG_RUNTIME_DIR`/the bus up for you.
> - **Or export `XDG_RUNTIME_DIR`** in the `su` shell before any `systemctl --user` (run this **as
>   the bot user**, not root — as root `id -u` is `0` and points at the wrong dir):
>   ```bash
>   export XDG_RUNTIME_DIR=/run/user/$(id -u)
>   ```
>   That alone is usually enough. If it still can't connect, also set the bus address explicitly:
>   ```bash
>   export DBUS_SESSION_BUS_ADDRESS=unix:path=$XDG_RUNTIME_DIR/bus
>   ```
>   Append the export(s) to the user's `~/.bashrc` so they stick. (`machinectl shell <user>@` also
>   gives a clean session but isn't installed on minimal/VPS hosts.)
>
> Enable **linger first** (`loginctl enable-linger <user>`, step 1) — it starts the user manager and
> creates `/run/user/<uid>`; the exports point at that dir, so without linger it won't exist yet.

> A user unit can't order itself `After=mariadb.service` (that's a system unit). On a Plesk host
> MariaDB is up long before the lingering user managers start at boot, so the DB is ready in
> practice; `Restart=on-failure` covers any rare early race. If you would rather use **root-owned**
> units instead, create `/etc/systemd/system/puzzbot.service` and `puzzbot-test.service` with
> `User=puzzbot` / `User=puzzbot-test`, `After=... mariadb.service`, and manage them with
> `sudo systemctl` — but then the Plesk deploy hook needs a passwordless-sudo rule per unit (see
> below).

### Redeploying with `deploy.sh` (Plesk Git)

[`deploy.sh`](deploy.sh) runs the post-pull steps in the right order: it syncs Python deps from the
lockfile (`pipenv sync`), applies pending DB migrations (`migrate.py`), then restarts the bot.
Migrations run **before** the restart, so a failed migration aborts the deploy and leaves the
running bot untouched (the migrate→deploy→code golden rule).

Wire it into **each** subdomain's **Websites & Domains → (your domain) → Git → Additional
deployment actions** so live and test redeploy independently from their own branches (`main` /
`staging`). Plesk pulls the repo and then runs that box as the domain's **subscription system
user** (Hosting Settings → *System user*), non-interactively — so arrange for that user to be the
instance's user (`puzzbot` / `puzzbot-test`), and the restart below then targets the right bot.

- **Restart via the user-level unit** — the natural fit here, since each bot runs under its own
  user's `systemctl --user`. It needs no root at all. Set `RESTART_CMD` inline in the
  deployment-actions box (Plesk has no separate env-var panel):

  ```bash
  RESTART_CMD='systemctl --user restart puzzbot' ./deploy.sh
  ```

  This works unattended because linger (step 1) keeps the user's systemd manager running even
  with no active login. The Plesk hook runs in a non-login shell with no `XDG_RUNTIME_DIR`, so a
  bare `systemctl --user` there would fail with *"Failed to connect to bus"* — `deploy.sh` sets
  `XDG_RUNTIME_DIR=/run/user/$(id -u)` for you to avoid that (linger must be on so the dir exists).
  Because the unit runs `start.sh`, the redeploy still rebuilds the frontend.

- **Alternative — root-owned units.** If you went with root units instead, use
  `RESTART_CMD='sudo -n systemctl restart puzzbot'` (or `puzzbot-test`) and add a one-time
  passwordless rule per unit as root (confirm the path with `command -v systemctl`):

  ```
  # /etc/sudoers.d/puzzbot
  puzzbot      ALL=(root) NOPASSWD: /usr/bin/systemctl restart puzzbot
  puzzbot-test ALL=(root) NOPASSWD: /usr/bin/systemctl restart puzzbot-test
  ```

- **No systemd at all?** Leave `RESTART_CMD` **unset** and `deploy.sh` relaunches the bot as a
  detached user process via `start.sh --restart` — zero privileges, but no supervision or
  reboot-persistence. Use this only if that instance is *not* also under a systemd unit; running
  both would start two bots fighting over the token and its port.

- **Don't restart the bot with a foreground command in the hook** — the deploy action must return.
  `deploy.sh` already detaches its fallback restart, and `systemctl restart` returns immediately,
  so either path is safe.

## 7. (Dashboard) reverse proxy with nginx *or* Apache + TLS

Quart serves the API and the SPA under one origin, so there is **one upstream and no CORS** — just
proxy your HTTPS hostname to the bot's `:8099`, including the WebSocket upgrade for the live
activity feed (`/api/ws/activity`). Terminate TLS at the proxy (Let's Encrypt for a public name, or
an internal CA for an internal one). Either web server works — use whichever your host already runs:

- **nginx** — the canonical block below, plus the Plesk nginx variant.
- **Apache** — the [equivalent directives](#apache-equivalent-directives) further down, plus the
  Plesk Apache variant. Use this if your Plesk box serves sites with Apache and has no nginx.

The bot reads **no** forwarded headers (it takes its URLs from `frontend_url` / `oauth_redirect_uri`
in `config.json`), so the `X-Forwarded-*` lines below are only for correct client IPs in logs —
the proxy works without them. What is **not** optional is the **WebSocket upgrade** for
`/api/ws/activity` and a **long read timeout**, or the live activity feed drops every 60s.

This is the canonical proxy block — the Apache equivalent, the Plesk variants below, and the
souwester test-site example in `docs/dashboard.md` are the same directives reshaped for their
environment. If you change the proxy headers, timeouts, or WebSocket upgrade handling, update them
all together.

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
outbound proxy connection** by default and you'll get `502` + an `AVC denied` audit log. Allow it
(this same `httpd_` boolean also covers Apache):

```bash
sudo setsebool -P httpd_can_network_connect 1
```

### Apache (equivalent directives)

Same proxy, Apache flavour. It needs `mod_proxy`, `mod_proxy_http`, `mod_proxy_wstunnel` (the
WebSocket tunnel), `mod_headers`, and `mod_ssl`. On Debian/Ubuntu:

```bash
sudo a2enmod proxy proxy_http proxy_wstunnel headers ssl http2
sudo systemctl reload apache2
```

On AlmaLinux/RHEL these ship in the `httpd` package and are usually loaded already (check
`/etc/httpd/conf.modules.d/`); on Plesk they are enabled by default.

The vhost (e.g. `/etc/apache2/sites-available/puzzbot.conf`, or `/etc/httpd/conf.d/puzzbot.conf`):

```apache
<VirtualHost *:443>
    ServerName your-host

    SSLEngine on
    SSLCertificateFile    /etc/letsencrypt/live/your-host/fullchain.pem
    SSLCertificateKeyFile /etc/letsencrypt/live/your-host/privkey.pem

    ProxyPreserveHost On
    RequestHeader set X-Forwarded-Proto "https"   # optional; logging only

    # WebSocket upgrade for the live activity feed. This explicit ws:// mapping
    # MUST come before the catch-all below — mod_proxy matches in file order.
    ProxyPass        /api/ws/activity ws://127.0.0.1:8099/api/ws/activity timeout=3600
    ProxyPassReverse /api/ws/activity ws://127.0.0.1:8099/api/ws/activity

    # Everything else (API + SPA). The long timeout keeps slow/idle requests alive.
    ProxyPass        / http://127.0.0.1:8099/ timeout=3600
    ProxyPassReverse / http://127.0.0.1:8099/
</VirtualHost>

# Optional: redirect plain HTTP to HTTPS
<VirtualHost *:80>
    ServerName your-host
    Redirect permanent / https://your-host/
</VirtualHost>
```

Validate and reload:

```bash
sudo apachectl configtest && sudo systemctl reload apache2   # httpd on RHEL
```

`ProxyPass /` proxies **all** requests, so Apache never serves the vhost's DocumentRoot — Quart
serves the SPA. As with nginx, if the bot and Apache are on **different boxes**, set `web.host` to
`0.0.0.0`, point the proxy at the bot box's LAN address, and firewall `:8099` to the proxy only.
The SELinux boolean above applies to Apache too.

### Plesk-managed hosts

Don't hand-edit vhosts on a Plesk box — Plesk owns the generated web-server config (nginx and/or
Apache) and will overwrite them. Add your proxy through Plesk's **Additional directives** boxes
instead. Use **step 3a** if Plesk has nginx in front, or **step 3b** if it serves with **Apache
only** (no nginx) — check under the subdomain's **Apache & nginx Settings**: if there is no
"Proxy mode" / nginx section, you're Apache-only.

The proxy blocks below are the same directives as the standalone blocks above (and the souwester
example in `docs/dashboard.md`) reshaped for where Plesk lets you add them — if you change the proxy
headers, timeouts, or WebSocket upgrade handling, update them all together.

Do steps 1–3 **once per instance**: two subdomains, each proxying to its own instance's port
(`puzzweb.<domain>` → `:8099` live, `puzzweb-test.<domain>` → `:8098` test). Step 4 (the nginx
WebSocket map) and step 5 (the SELinux boolean) are host-wide — do them **once** and both subdomains
use them.

1. **Create a subdomain** for the dashboard in **Websites & Domains** (e.g.
   `puzzweb.your-domain.com` for live, `puzzweb-test.your-domain.com` for test). Point each
   instance's `oauth_redirect_uri` / `frontend_url` / Discord redirect at its own hostname.
2. **Issue TLS** with the **SSL It!** extension (Let's Encrypt) for that subdomain — Plesk handles
   renewal.
3a. **(nginx front-end) Turn the docroot into a pure proxy.** Under the subdomain → **Apache &
   nginx Settings**:
   - tick **Proxy mode** off-loaded to nginx as needed, and **untick "Smart static files
     processing"** so nginx doesn't try to serve files from the docroot (Quart serves the SPA).
   - In **Additional nginx directives**, add the proxy (Plesk drops these into the `server{}` block,
     so reference a map defined globally — see step 4):
     ```nginx
     location / {
         proxy_pass http://127.0.0.1:8099;   # test subdomain → 127.0.0.1:8098
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

3b. **(Apache-only front-end) Add the proxy in Apache's directives box.** Under the subdomain →
   **Apache & nginx Settings** → **Additional Apache directives**, use the box for **HTTPS**
   (Plesk already generated the `<VirtualHost *:443>` and its TLS; you're adding inside it, so no
   `<VirtualHost>` wrapper and no SSL cert lines). Apache-only means **no** nginx map step 4 is
   needed — `mod_proxy_wstunnel` handles the upgrade via the explicit `ws://` line:
   ```apache
   ProxyPreserveHost On

   # WebSocket first — must precede the catch-all (mod_proxy matches in order).
   ProxyPass        /api/ws/activity ws://127.0.0.1:8099/api/ws/activity timeout=3600
   ProxyPassReverse /api/ws/activity ws://127.0.0.1:8099/api/ws/activity

   ProxyPass        / http://127.0.0.1:8099/ timeout=3600   # test subdomain → :8098
   ProxyPassReverse / http://127.0.0.1:8099/
   ```
   `ProxyPass /` overrides the docroot, so there is no "Smart static files" toggle to worry about.
   Confirm `mod_proxy_wstunnel` is loaded (`httpd -M | grep wstunnel`); on Plesk it normally is.

   **Alternative — detect the upgrade by header (`mod_rewrite`).** If the explicit `ws://`
   `ProxyPass` line above doesn't upgrade on your Apache/module build (some do, some don't),
   use a `mod_rewrite` rule that proxies *any* request carrying an `Upgrade: websocket` header
   instead. Keep the catch-all `ProxyPass /` and add:
   ```apache
   ProxyPreserveHost On
   ProxyPass        / http://127.0.0.1:8099/ timeout=3600   # test subdomain → :8098
   ProxyPassReverse / http://127.0.0.1:8099/

   # Any WebSocket (matched by the Upgrade header) → ws:// backend.
   RewriteEngine On
   RewriteCond %{HTTP:Upgrade} =websocket [NC]
   RewriteRule /(.*) ws://127.0.0.1:8099/$1 [P,L]
   ```
   This form is path-agnostic (no hard-coded `/api/ws/activity`) but needs **`mod_rewrite`** in
   addition to `mod_proxy_wstunnel`. If WebSockets still don't upgrade, move the `RewriteEngine`
   block **above** the `ProxyPass /` line — module load order decides which claims the request
   first, so rewrite-first is the safer arrangement.

   > **nginx in front of Apache?** If Plesk fronts Apache with nginx (there's a "Proxy mode"
   > section under **Apache & nginx Settings** → you're *not* Apache-only), these Apache
   > directives only fix the nginx→Apache→app inner hop — nginx still has to forward the
   > `Upgrade`/`Connection` headers on the outer hop. Recent Plesk does by default; if plain
   > pages work but the WebSocket never returns **101**, nginx is dropping the header. Fix it by
   > carving the WS path out in **Additional nginx directives** (proxied straight to the app,
   > bypassing Apache for just that path):
   > ```nginx
   > location /api/ws/ {
   >     proxy_pass http://127.0.0.1:8099;   # test subdomain → :8098
   >     proxy_http_version 1.1;
   >     proxy_set_header Upgrade    $http_upgrade;
   >     proxy_set_header Connection "upgrade";
   >     proxy_set_header Host       $host;
   >     proxy_read_timeout 3600s;
   > }
   > ```
4. **(nginx only) Define the WebSocket upgrade map once, globally** (the `map` directive is only
   valid in `http{}` context, which Plesk's per-domain box is not). Plesk includes
   `/etc/nginx/conf.d/*.conf` in its `http{}`, so add a file there with a uniquely-named variable to
   avoid clashing with any Plesk-provided map. **Skip this entirely on an Apache-only host.**
   ```nginx
   # /etc/nginx/conf.d/puzzbot_ws.conf
   map $http_upgrade $puzzbot_conn_upgrade { default upgrade; '' close; }
   ```
   Then `sudo nginx -t && sudo systemctl reload nginx`. (Plesk also regenerates config on
   subscription changes; this `conf.d` file is global and survives that.)
5. **SELinux** still applies — run the `httpd_can_network_connect` boolean above so Plesk's
   nginx/Apache can reach `127.0.0.1:8099`.

Keep the bot's `web.host` at `127.0.0.1` (same-box) so `:8099` is never exposed directly — only
Plesk's proxy reaches it.

## 8. Verify

Check **each** instance independently — the two are only truly isolated if these all pass separately:

- Each bot logs in to Discord as its **own** application and responds to `!help` (confirm the live
  bot answers in the live server and the test bot as the test app — not one bot answering twice).
- Browse to each subdomain (`https://puzzweb.<domain>/` and `https://puzzweb-test.<domain>/`),
  click login → you are bounced to Discord, authorise, and land back on that board.
- Confirm the ports don't collide: `ss -ltnp | grep -E ':809[89]'` should show `puzzbot` on `8099`
  and `puzzbot-test` on `8098` (and both `systemctl --user status puzzbot` are `active`).
- The puzzle list and live activity feed populate (an active hunt is visible to holders of its
  team role; see [`docs/dashboard.md`](docs/dashboard.md) for the visibility model).

## Tests

Use `pipenv install --dev` to install dev packages, and in the repo root directory, run
```bash
timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1
```
Use `pipenv run python -m pytest` (not a bare `pytest`): that is what puts the repo root
on `sys.path`, so `import bot` resolves. Wrap the run in `timeout` and write the output to
a file — a run killed by the timeout exits 124 and can lose buffered output from a pipe.
Narrow to one file or `-k` filter while iterating.

# Credits

Inspired by various open source discord bot python projects like [cookiecutter-discord.py-postgres](https://github.com/makupi/cookiecutter-discord.py-postgres) and [discord-pretty-help](https://github.com/stroupbslayen/discord-pretty-help/). Licensed under [GPL 3.0](https://choosealicense.com/licenses/gpl-3.0/) (due to the aforementioned `cookiecutter`).
